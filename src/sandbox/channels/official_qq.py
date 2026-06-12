"""
OfficialQQChannel —— 官方 QQ 机器人渠道，通过 WebSocket 连接 QQ Bot API v2。

============================================================================
协议说明
============================================================================

  官方 QQ 机器人使用 QQ Bot API v2 协议。
  通信方式: HpAgent 作为 WebSocket 客户端主动连接 QQ 网关，
  接收事件推送，通过 HTTP REST API 发送消息。

  WebSocket OpCode 流程:
    1. GET /gateway/bot → 获取 WSS URL
    2. 连接 WSS → 收到 OpCode 10 Hello (heartbeat_interval)
    3. 发送 OpCode 2 Identify (token + intents + shard)
    4. 收到 OpCode 0 Dispatch READY (session_id)
    5. 循环: OpCode 1 Heartbeat ↔ OpCode 11 Heartbeat ACK
    6. 收到 OpCode 0 Dispatch 各种事件 → normalize → callback

  断线重连:
    7. 连接断开 → 发送 OpCode 6 Resume (session_id + seq)
    8. 恢复成功 → 继续接收事件
    9. 恢复失败 → 重新 Identify

  消息事件类型:
    - GROUP_AT_MESSAGE_CREATE: 群聊 @机器人消息 (intent 1<<25)
    - C2C_MESSAGE_CREATE:      单聊消息 (intent 1<<25)
    - AT_MESSAGE_CREATE:       频道文字子频道 @机器人 (intent 1<<30)
    - DIRECT_MESSAGE_CREATE:   频道私信 (intent 1<<12)
    - INTERACTION_CREATE:      按钮回调 (intent 1<<26)
    - MESSAGE_AUDIT_PASS:      消息审核通过
    - MESSAGE_AUDIT_REJECT:    消息审核拒绝

  发送消息 (HTTP REST API):
    - C2C:    POST /v2/users/{openid}/messages
    - Group:  POST /v2/groups/{group_openid}/messages
    - Channel: POST /channels/{channel_id}/messages
    - DM:     POST /dms/{guild_id}/messages

============================================================================
认证说明
============================================================================

  使用 OAuth2 风格的 access_token:
    1. POST https://bots.qq.com/app/getAppAccessToken
       请求体: {"appId": "APPID", "clientSecret": "CLIENTSECRET"}
    2. 响应: {"access_token": "ACCESS_TOKEN", "expires_in": "7200"}
    3. 所有 HTTP API 请求头: Authorization: QQBot {ACCESS_TOKEN}
    4. token 有效期 7200s，后台 task 每 7000s 自动刷新

============================================================================
配置环境变量
============================================================================

  QQ_OFFICIAL_APP_ID          — 机器人 AppID
  QQ_OFFICIAL_CLIENT_SECRET   — 机器人 ClientSecret
  QQ_OFFICIAL_SANDBOX         — 是否使用沙箱环境 (默认 false)
"""
import asyncio
import json
import logging
import os
import time
from typing import Any, Optional, Callable, Awaitable

import aiohttp
import websockets

from sandbox.channels.base import BaseChannel
from common.types import ChannelType, UnifiedMessage

logger = logging.getLogger("HpAgent")

# ═══════════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════════

TOKEN_URL = "https://bots.qq.com/app/getAppAccessToken"
GATEWAY_URL = "https://api.sgroup.qq.com/gateway/bot"
SANDBOX_GATEWAY_URL = "https://sandbox.api.sgroup.qq.com/gateway/bot"
API_BASE = "https://api.sgroup.qq.com"
SANDBOX_API_BASE = "https://sandbox.api.sgroup.qq.com"

# Token 提前刷新时间（7200 - 200 = 7000s）
TOKEN_REFRESH_INTERVAL = 7000

# 重连退避参数
RECONNECT_BASE_DELAY = 2.0
RECONNECT_MAX_DELAY = 60.0
RECONNECT_BACKOFF_FACTOR = 1.5

# 消息去重缓存 TTL（秒）
DEDUP_TTL = 60

# 发送间隔（秒）—— 防风控
SEND_INTERVAL = 0.7

# Intents 位掩码: GUILDS | PUBLIC_GUILD_MESSAGES | GROUP_AND_C2C_EVENT | DIRECT_MESSAGE | INTERACTION | MESSAGE_AUDIT
_DEFAULT_INTENTS = (
    (1 << 0)    # GUILDS
    | (1 << 12)  # DIRECT_MESSAGE
    | (1 << 25)  # GROUP_AND_C2C_EVENT
    | (1 << 26)  # INTERACTION
    | (1 << 27)  # MESSAGE_AUDIT
    | (1 << 30)  # PUBLIC_GUILD_MESSAGES
)


# ═══════════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════════

def _safe_str(value: Any, default: str = "") -> str:
    """安全转为字符串，处理 None/空值。"""
    if value is None:
        return default
    return str(value)


# ═══════════════════════════════════════════════════════════════════════════════
# OfficialQQChannel
# ═══════════════════════════════════════════════════════════════════════════════

class OfficialQQChannel(BaseChannel):
    """官方 QQ 机器人渠道 —— 连接 QQ Bot API v2 网关，收发消息。

    架构:
      QQ 网关 ──WebSocket──→ HpAgent (作为 WS 客户端连接)
        ↓ normalize_message() → UnifiedMessage
        ↓ callback → Worker.handle_message()
        ↓ agentic loop 处理
        ↓ ChannelRouter.send() → send_message()
        ↓ HTTP POST → QQ REST API → QQ 平台

    Attributes:
        _app_id: 机器人 AppID。
        _client_secret: 机器人 ClientSecret。
        _sandbox: 是否使用沙箱环境。
        _access_token: 当前有效的 access_token。
        _token_lock: access_token 并发保护锁。
        _session: aiohttp ClientSession（复用 HTTP 连接）。
        _ws: WebSocket 连接。
        _session_id: WSS 会话 ID（断线重连用）。
        _last_seq: 最后收到的 s 值（断线重连用）。
        _heartbeat_task: 心跳发送 task。
        _token_refresh_task: token 刷新 task。
        _reconnect_task: 重连 task。
        _sent_msg_ids: 已处理消息 ID 去重集合（含 TTL）。
        _last_send_time: 上次发送时间（防风控）。
        _shutdown: 关闭信号。
    """

    def __init__(self):
        super().__init__(ChannelType.OFFICIAL_QQ)
        self._app_id = os.getenv("QQ_OFFICIAL_APP_ID", "")
        self._client_secret = os.getenv("QQ_OFFICIAL_CLIENT_SECRET", "")
        self._sandbox = os.getenv("QQ_OFFICIAL_SANDBOX", "false").lower() in ("true", "1", "yes")

        self._access_token: str = ""
        self._token_lock = asyncio.Lock()
        self._session: Optional[aiohttp.ClientSession] = None

        # WebSocket 连接状态
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._session_id: str = ""
        self._last_seq: Optional[int] = None

        # 后台 task 引用
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._token_refresh_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        # 去重 & 风控
        self._sent_msg_ids: dict[str, float] = {}
        self._last_send_time: float = 0.0

        # 关闭信号
        self._shutdown = False

        # 网关地址（启动时从 /gateway/bot 获取）
        self._wss_url = ""
        self._api_base = SANDBOX_API_BASE if self._sandbox else API_BASE

    # ═════════════════════════════════════════════════════════════════════════
    # 认证
    # ═════════════════════════════════════════════════════════════════════════

    async def _fetch_token(self) -> str:
        """获取或刷新 access_token。

        调用 QQ Bot token 端点，使用 appId + clientSecret 获取 token。
        并发安全（asyncio.Lock 保护）。

        Returns:
            access_token 字符串。

        Raises:
            RuntimeError: AppID 或 ClientSecret 未配置。
            aiohttp.ClientError: HTTP 请求失败。
            ValueError: 响应格式异常。
        """
        if not self._app_id or not self._client_secret:
            raise RuntimeError(
                "QQ_OFFICIAL_APP_ID and QQ_OFFICIAL_CLIENT_SECRET are required. "
                "Set them as environment variables."
            )

        async with self._token_lock:
            payload = {"appId": self._app_id, "clientSecret": self._client_secret}
            async with self._session.post(TOKEN_URL, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise aiohttp.ClientResponseError(
                        resp.request_info, resp.history, status=resp.status, message=body
                    )
                data = await resp.json()

            token = data.get("access_token", "")
            if not token:
                raise ValueError(f"access_token missing in response: {data}")
            expires_in = data.get("expires_in", 7200)

            self._access_token = token
            logger.info(
                "QQ official bot token refreshed: expires_in=%ss, sandbox=%s",
                expires_in, self._sandbox,
            )
            return token

    async def _get_token(self) -> str:
        """获取当前有效的 access_token（首次调用会自动获取）。

        Returns:
            access_token 字符串。
        """
        if not self._access_token:
            await self._fetch_token()
        return self._access_token

    async def _token_refresh_loop(self):
        """后台循环：每 TOKEN_REFRESH_INTERVAL 秒刷新一次 access_token。"""
        while not self._shutdown:
            try:
                await asyncio.sleep(TOKEN_REFRESH_INTERVAL)
                if self._shutdown:
                    break
                await self._fetch_token()
                logger.debug("QQ official bot token auto-refreshed")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("QQ official bot token refresh failed: %s", e)
                # 失败不中止，下次继续尝试

    # ═════════════════════════════════════════════════════════════════════════
    # WebSocket 网关连接
    # ═════════════════════════════════════════════════════════════════════════

    async def _get_gateway_url(self) -> str:
        """调用 /gateway/bot 获取 WebSocket 网关地址。

        Returns:
            WebSocket URL 字符串。
        """
        token = await self._get_token()
        gateway_endpoint = SANDBOX_GATEWAY_URL if self._sandbox else GATEWAY_URL
        headers = {"Authorization": f"QQBot {token}"}

        async with self._session.get(gateway_endpoint, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise aiohttp.ClientResponseError(
                    resp.request_info, resp.history, status=resp.status, message=body
                )
            data = await resp.json()

        wss_url = data.get("url", "")
        if not wss_url:
            raise ValueError(f"WSS url missing in gateway response: {data}")

        shards = data.get("shards", 1)
        logger.info(
            "QQ official bot gateway: url=%s shards=%d",
            wss_url, shards,
        )
        return wss_url

    async def _send_ws(self, op: int, d: Any = None):
        """向 QQ 网关发送 WebSocket 消息。

        Args:
            op: OpCode 数值。
            d: 数据 payload。
        """
        if self._ws is None:
            return
        try:
            payload = json.dumps({"op": op, "d": d})
            await self._ws.send(payload)
        except (websockets.exceptions.ConnectionClosed, websockets.exceptions.ConnectionClosedError):
            logger.debug("QQ gateway: send skipped, connection closed")
        except Exception as e:
            logger.warning("QQ gateway: send error: %s", e)

    async def _handle_hello(self, data: dict):
        """处理 OpCode 10 Hello —— 启动心跳并发送 Identify。

        Args:
            data: Hello payload，含 heartbeat_interval (ms)。
        """
        heartbeat_interval_ms = data.get("heartbeat_interval", 41250)
        heartbeat_interval_s = max(heartbeat_interval_ms / 1000.0, 10.0)
        logger.info("QQ gateway Hello: heartbeat_interval=%.1fs", heartbeat_interval_s)

        # 启动心跳 task（先取消旧的）
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(heartbeat_interval_s)
        )

        # 发送 Identify
        token = await self._get_token()
        identify_payload = {
            "token": f"QQBot {token}",
            "intents": _DEFAULT_INTENTS,
            "shard": [0, 1],
            "properties": {},
        }
        await self._send_ws(2, identify_payload)
        logger.info("QQ gateway Identify sent: intents=%d", _DEFAULT_INTENTS)

    async def _heartbeat_loop(self, interval_s: float):
        """后台循环：按 interval_s 定时发送 OpCode 1 Heartbeat。

        Args:
            interval_s: 心跳间隔（秒）。
        """
        while not self._shutdown:
            try:
                await asyncio.sleep(interval_s)
                if self._shutdown:
                    break
                # d 字段填最新的 s 值（如果是 null 填 null）
                heartbeat_d = self._last_seq if self._last_seq is not None else None
                await self._send_ws(1, heartbeat_d)
                logger.debug("QQ gateway heartbeat sent: seq=%s", heartbeat_d)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("QQ gateway heartbeat error: %s", e)

    # ═════════════════════════════════════════════════════════════════════════
    # 消息标准化
    # ═════════════════════════════════════════════════════════════════════════

    async def normalize_message(self, raw_message: Any) -> Optional[UnifiedMessage]:
        """将 QQ Bot 事件标准化为 UnifiedMessage。

        支持的事件类型:
          - GROUP_AT_MESSAGE_CREATE → 群聊 @机器人
          - C2C_MESSAGE_CREATE       → 单聊消息
          - AT_MESSAGE_CREATE        → 频道子频道 @机器人
          - DIRECT_MESSAGE_CREATE    → 频道私信
          - INTERACTION_CREATE       → 按钮点击回调
          - MESSAGE_AUDIT_PASS/REJECT（仅记录，不触发回复）

        Args:
            raw_message: 原始 QQ Bot 事件 payload（dict）。

        Returns:
            UnifiedMessage 对象，无法识别的消息类型返回 None。
        """
        if isinstance(raw_message, str):
            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                raise ValueError(f"QQ official bot: Invalid JSON message: {raw_message}")
        elif isinstance(raw_message, dict):
            data = raw_message
        else:
            raise ValueError(f"QQ official bot: Unsupported message type: {type(raw_message)}")

        event_type = data.get("t", "")
        event_data = data.get("d", {})

        if not event_type:
            return None

        metadata: dict[str, Any] = {
            "detail_type": None,
            "event_type": event_type,
            "msg_id": event_data.get("id"),
            "timestamp": event_data.get("timestamp", ""),
        }

        sender_id = ""
        content = ""

        # ── C2C 单聊消息 ──
        if event_type == "C2C_MESSAGE_CREATE":
            author = event_data.get("author", {})
            sender_id = _safe_str(author.get("id") or author.get("user_openid"))
            content = event_data.get("content", "")
            metadata["detail_type"] = "private"
            metadata["user_openid"] = sender_id

            # 附件
            attachments = event_data.get("attachments", []) or []
            if attachments:
                urls = [a.get("url", "") for a in attachments if a.get("url")]
                metadata["attachments"] = urls

        # ── 群聊 @机器人 ──
        elif event_type == "GROUP_AT_MESSAGE_CREATE":
            author = event_data.get("author", {})
            sender_id = _safe_str(author.get("id") or author.get("member_openid"))
            content = event_data.get("content", "")
            group_openid = event_data.get("group_openid") or event_data.get("group_id")
            metadata["detail_type"] = "group"
            metadata["group_openid"] = _safe_str(group_openid)
            metadata["member_openid"] = sender_id

            # 附件
            attachments = event_data.get("attachments", []) or []
            if attachments:
                urls = [a.get("url", "") for a in attachments if a.get("url")]
                metadata["attachments"] = urls

        # ── 频道文字子频道 @机器人 ──
        elif event_type == "AT_MESSAGE_CREATE":
            author = event_data.get("author", {})
            sender_id = _safe_str(author.get("id"))
            content = event_data.get("content", "")
            metadata["detail_type"] = "guild"
            metadata["guild_id"] = event_data.get("guild_id")
            metadata["channel_id"] = event_data.get("channel_id")

        # ── 频道私信 ──
        elif event_type == "DIRECT_MESSAGE_CREATE":
            author = event_data.get("author", {})
            sender_id = _safe_str(author.get("id"))
            content = event_data.get("content", "")
            metadata["detail_type"] = "dm"
            metadata["guild_id"] = event_data.get("guild_id")

        # ── 按钮交互回调 ──
        elif event_type == "INTERACTION_CREATE":
            # interaction 的数据结构不同，id/user_id 在外层
            sender_id = _safe_str(event_data.get("user_openid") or "")
            interaction_data = event_data.get("data", {})
            # 按钮的 label 作为 content
            button_label = (interaction_data.get("resolved", {}) or {}).get("button_data", "")
            content = button_label or interaction_data.get("target", "")
            metadata["detail_type"] = "interaction"
            metadata["interaction_id"] = event_data.get("id")

        # ── 消息审核通过 —— 提取审核通过的消息内容继续处理 ──
        elif event_type == "MESSAGE_AUDIT_PASS":
            audit_data = event_data.get("audit_data", {})
            audit_op = audit_data.get("audit_op", "")
            metadata["detail_type"] = "audit_pass"
            metadata["audit_op"] = audit_op
            # 审核通过不触发 agent 处理，仅记录
            logger.debug("Message audit pass: msg_id=%s op=%s", event_data.get("message_id"), audit_op)
            return None

        # ── 消息审核拒绝 —— 仅记录 ──
        elif event_type == "MESSAGE_AUDIT_REJECT":
            metadata["detail_type"] = "audit_reject"
            logger.debug(
                "Message audit reject: msg_id=%s reason=%s",
                event_data.get("message_id"), event_data.get("audit_reason"),
            )
            return None

        # ── GUILD / CHANNEL / MEMBER 等非消息事件 —— 忽略 ──
        elif event_type in (
            "GUILD_CREATE", "GUILD_UPDATE", "GUILD_DELETE",
            "CHANNEL_CREATE", "CHANNEL_UPDATE", "CHANNEL_DELETE",
            "GUILD_MEMBER_ADD", "GUILD_MEMBER_UPDATE", "GUILD_MEMBER_REMOVE",
            "MESSAGE_REACTION_ADD", "MESSAGE_REACTION_REMOVE",
            "FRIEND_ADD", "FRIEND_DEL",
            "GROUP_ADD_ROBOT", "GROUP_DEL_ROBOT", "GROUP_MSG_REJECT", "GROUP_MSG_RECEIVE",
            "FORUM_THREAD_CREATE", "FORUM_THREAD_UPDATE", "FORUM_THREAD_DELETE",
            "FORUM_POST_CREATE", "FORUM_POST_DELETE",
            "FORUM_REPLY_CREATE", "FORUM_REPLY_DELETE",
            "AUDIO_START", "AUDIO_FINISH", "AUDIO_ON_MIC", "AUDIO_OFF_MIC",
        ):
            logger.debug("QQ gateway event ignored: %s", event_type)
            return None

        # ── HTTP Callback ACK (OpCode 12) ──
        # 这是在 _handle_dispatch 层面处理的，不会到这里
        else:
            logger.debug("QQ gateway unhandled event: %s", event_type)
            return None

        return UnifiedMessage(
            sender_id=sender_id,
            content=content,
            channel_type=ChannelType.OFFICIAL_QQ,
            metadata=metadata,
        )

    # ═════════════════════════════════════════════════════════════════════════
    # 消息发送
    # ═════════════════════════════════════════════════════════════════════════

    async def send_message(self, message: UnifiedMessage) -> bool:
        """通过 QQ REST API 发送消息。

        根据 metadata 中的 detail_type 选择 API 端点:
          - "private" → POST /v2/users/{user_openid}/messages
          - "group"   → POST /v2/groups/{group_openid}/messages
          - "guild"   → POST /channels/{channel_id}/messages
          - "dm"      → POST /dms/{guild_id}/messages

        Args:
            message: 统一消息对象，metadata 中需含 detail_type 和对应的目标 ID。

        Returns:
            发送是否成功。
        """
        if not message.content or not message.content.strip():
            return True

        detail_type = message.metadata.get("detail_type")
        if not detail_type:
            logger.error("QQ official bot: missing detail_type in metadata")
            return False

        # 防风控：控制发送间隔
        await self._rate_limit()

        # 构造 API 请求
        url, payload = self._build_send_payload(message, detail_type)
        if url is None:
            return False

        # 被动回复标记
        msg_id = message.metadata.get("msg_id")
        if msg_id:
            payload["msg_id"] = msg_id

        payload["msg_type"] = 0  # 纯文本

        # 去重检查
        dedup_key = f"{url}:{message.content[:50]}"
        now = time.time()
        self._clean_dedup_cache(now)
        if dedup_key in self._sent_msg_ids:
            logger.debug("QQ official bot: duplicate message skipped")
            return True
        self._sent_msg_ids[dedup_key] = now

        # 发送 HTTP 请求
        try:
            token = await self._get_token()
            headers = {
                "Authorization": f"QQBot {token}",
                "Content-Type": "application/json",
            }
            async with self._session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 200 or resp.status == 204:
                    logger.info("QQ official bot: message sent to %s → %s", detail_type, url)
                    return True
                elif resp.status == 401:
                    # Token 过期，刷新后重试一次
                    logger.warning("QQ official bot: token expired, refreshing...")
                    await self._fetch_token()
                    token = await self._get_token()
                    headers["Authorization"] = f"QQBot {token}"
                    async with self._session.post(url, json=payload, headers=headers) as resp2:
                        if resp2.status in (200, 204):
                            logger.info("QQ official bot: message sent after token refresh")
                            return True
                        body = await resp2.text()
                        logger.error("QQ official bot: send failed after refresh: %s %s", resp2.status, body)
                        return False
                else:
                    body = await resp.text()
                    logger.error("QQ official bot: send failed: %s %s", resp.status, body)
                    return False
        except Exception as e:
            logger.error("QQ official bot: send error: %s", e)
            return False

    def _build_send_payload(
        self, message: UnifiedMessage, detail_type: str
    ) -> tuple[Optional[str], dict]:
        """根据 detail_type 构造 HTTP API URL 和请求体。

        Args:
            message: 统一消息对象。
            detail_type: 消息详细类型。

        Returns:
            (url, payload) 元组，解析失败时返回 (None, {})。
        """
        content = message.content

        if detail_type == "private":
            user_openid = message.metadata.get("user_openid") or message.sender_id
            if not user_openid:
                logger.error("QQ official bot: private msg missing user_openid")
                return None, {}
            url = f"{self._api_base}/v2/users/{user_openid}/messages"
            payload = {"content": content}

        elif detail_type == "group":
            group_openid = message.metadata.get("group_openid")
            if not group_openid:
                logger.error("QQ official bot: group msg missing group_openid")
                return None, {}
            url = f"{self._api_base}/v2/groups/{group_openid}/messages"
            payload = {"content": content}

        elif detail_type == "guild":
            channel_id = message.metadata.get("channel_id")
            if not channel_id:
                logger.error("QQ official bot: guild msg missing channel_id")
                return None, {}
            url = f"{self._api_base}/channels/{channel_id}/messages"
            payload = {"content": content}

        elif detail_type == "dm":
            guild_id = message.metadata.get("guild_id")
            if not guild_id:
                logger.error("QQ official bot: dm msg missing guild_id")
                return None, {}
            url = f"{self._api_base}/dms/{guild_id}/messages"
            payload = {"content": content}

        else:
            logger.error("QQ official bot: unsupported detail_type: %s", detail_type)
            return None, {}

        return url, payload

    async def _rate_limit(self):
        """发送间隔控制 —— 两次发送至少间隔 SEND_INTERVAL 秒。"""
        now = time.monotonic()
        elapsed = now - self._last_send_time
        if elapsed < SEND_INTERVAL:
            await asyncio.sleep(SEND_INTERVAL - elapsed)
        self._last_send_time = time.monotonic()

    def _clean_dedup_cache(self, now: float):
        """清理过期的去重缓存条目。"""
        stale = [k for k, ts in self._sent_msg_ids.items() if now - ts > DEDUP_TTL]
        for k in stale:
            del self._sent_msg_ids[k]

    # ═════════════════════════════════════════════════════════════════════════
    # 事件处理（Dispatch）
    # ═════════════════════════════════════════════════════════════════════════

    async def _handle_dispatch(self, data: dict):
        """处理 OpCode 0 Dispatch 事件。

        READY 事件更新 session_id，其他事件通过 normalize + callback 传递。

        Args:
            data: Dispatch 的完整 payload（含 op, d, s, t）。
        """
        seq = data.get("s")
        if seq is not None:
            self._last_seq = seq

        event_type = data.get("t", "")

        # ── READY 事件 ──
        if event_type == "READY":
            self._session_id = data.get("d", {}).get("session_id", "")
            logger.info("QQ gateway ready: session_id=%s", self._session_id[:8] + "..." if self._session_id else "empty")
            return

        # ── 消息事件 → normalize → callback ──
        try:
            channel_message = await self.normalize_message(data)
            if channel_message is None:
                return

            # 去重：根据 msg_id 检查是否已处理
            msg_id = channel_message.metadata.get("msg_id")
            if msg_id:
                now = time.time()
                self._clean_dedup_cache(now)
                if msg_id in self._sent_msg_ids:
                    logger.debug("QQ official bot: duplicate event skipped: %s", msg_id)
                    return
                self._sent_msg_ids[msg_id] = now

            if self._callback:
                await self._callback(channel_message)

            detail_type = channel_message.metadata.get("detail_type", "")
            logger.info(
                "QQ official bot: received %s (%s/%s) from %s: %s",
                event_type, detail_type,
                channel_message.metadata.get("group_openid", ""),
                channel_message.sender_id,
                channel_message.content[:80],
            )

        except Exception as e:
            logger.error("QQ official bot: dispatch handling error: %s", e)

    # ═════════════════════════════════════════════════════════════════════════
    # WebSocket 主循环
    # ═════════════════════════════════════════════════════════════════════════

    async def _ws_loop(self):
        """WebSocket 主循环 —— 连接 QQ 网关并处理消息。

        流程:
          1. 获取 WSS URL
          2. 连接 WebSocket
          3. 等待 Hello → 启动心跳 → 发送 Identify
          4. 循环接收 Dispatch 事件
          5. 断线后尝试 Resume 或重连
        """
        reconnect_delay = RECONNECT_BASE_DELAY

        while not self._shutdown:
            try:
                # 获取网关地址
                if not self._wss_url:
                    self._wss_url = await self._get_gateway_url()

                logger.info("QQ official bot: connecting to gateway %s", self._wss_url)
                self._ws = await websockets.connect(
                    self._wss_url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                )
                logger.info("QQ official bot: connected to gateway")

                # 接收消息循环
                async for message in self._ws:
                    if self._shutdown:
                        break
                    try:
                        data = json.loads(message)
                    except json.JSONDecodeError:
                        logger.warning("QQ official bot: invalid JSON from gateway")
                        continue

                    op = data.get("op")

                    if op == 10:  # Hello
                        await self._handle_hello(data.get("d", {}))

                    elif op == 0:  # Dispatch
                        await self._handle_dispatch(data)

                    elif op == 7:  # Reconnect —— 服务端要求重连
                        logger.info("QQ gateway requested reconnect")
                        break

                    elif op == 9:  # Invalid Session
                        logger.warning("QQ gateway: invalid session, will re-identify")
                        self._session_id = ""
                        self._last_seq = None
                        break

                    elif op == 11:  # Heartbeat ACK
                        logger.debug("QQ gateway heartbeat ACK")

                    else:
                        logger.debug("QQ gateway unknown opcode: %d", op)

            except websockets.exceptions.ConnectionClosed as e:
                logger.warning("QQ official bot: connection closed: %s", e)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("QQ official bot: ws loop error: %s", e)

            # 清理资源
            self._ws = None
            if self._heartbeat_task and not self._heartbeat_task.done():
                self._heartbeat_task.cancel()

            if self._shutdown:
                break

            # 断线重连
            logger.info(
                "QQ official bot: reconnecting in %.1fs...", reconnect_delay
            )
            try:
                await asyncio.sleep(reconnect_delay)
            except asyncio.CancelledError:
                break

            reconnect_delay = min(
                reconnect_delay * RECONNECT_BACKOFF_FACTOR,
                RECONNECT_MAX_DELAY,
            )

    # ═════════════════════════════════════════════════════════════════════════
    # 生命周期管理
    # ═════════════════════════════════════════════════════════════════════════

    async def start_monitor(
        self, callback: Optional[Callable[[UnifiedMessage], Awaitable[None]]] = None
    ) -> bool:
        """启动官方 QQ 机器人消息监控。

        1. 验证必要配置（AppID + ClientSecret）
        2. 获取首次 access_token
        3. 连接 QQ WebSocket 网关
        4. 启动 token 自动刷新循环

        Args:
            callback: 消息回调函数，收到新消息时调用。

        Returns:
            启动是否成功。
        """
        if not self._app_id or not self._client_secret:
            logger.error(
                "QQ official bot: QQ_OFFICIAL_APP_ID and QQ_OFFICIAL_CLIENT_SECRET "
                "are required. Channel not started."
            )
            return False

        self._monitoring = True
        self._callback = callback
        self._shutdown = False

        # 创建 aiohttp session
        self._session = aiohttp.ClientSession()

        try:
            # 获取首次 token
            await self._fetch_token()
        except Exception as e:
            logger.error("QQ official bot: initial token fetch failed: %s", e)
            await self._session.close()
            self._session = None
            return False

        # 启动 token 自动刷新
        self._token_refresh_task = asyncio.create_task(self._token_refresh_loop())

        # 启动 WebSocket 连接
        self._reconnect_task = asyncio.create_task(self._ws_loop())

        logger.info("QQ official bot channel started: sandbox=%s", self._sandbox)
        return True

    async def stop_monitor(self) -> bool:
        """停止官方 QQ 机器人消息监控。

        流程:
          1. 设置关闭信号
          2. 取消所有后台 task
          3. 关闭 WebSocket 连接
          4. 关闭 HTTP session

        Returns:
            是否停止成功。
        """
        self._monitoring = False
        self._callback = None
        self._shutdown = True

        # 取消后台 task
        for task_name, task in [
            ("heartbeat", self._heartbeat_task),
            ("token_refresh", self._token_refresh_task),
            ("reconnect", self._reconnect_task),
        ]:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.warning("QQ official bot: %s task stop error: %s", task_name, e)

        self._heartbeat_task = None
        self._token_refresh_task = None
        self._reconnect_task = None

        # 关闭 WebSocket
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

        # 关闭 HTTP session
        if self._session:
            await self._session.close()
            self._session = None

        logger.info("QQ official bot channel stopped")
        return True
