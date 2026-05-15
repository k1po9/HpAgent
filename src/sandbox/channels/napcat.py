"""
NapCatChannel —— NapCat QQ 协议通道，通过 WebSocket 连接 OneBot v11 客户端。

============================================================================
协议说明
============================================================================

  NapCat 是基于 OneBot v11 标准的 QQ 机器人框架。
  通信方式: 正向 WebSocket（HpAgent 作为服务端，NapCat 客户端主动连接）。

  OneBot v11 事件类型（post_type）:
    - message:     消息事件（私聊 private / 群聊 group / 频道 guild）
    - notice:      通知事件（群文件上传、管理员变动、成员增减、禁言等）
    - request:     请求事件（加好友、加群 / 邀请入群）
    - meta_event:  元事件（生命周期 connect、心跳 heartbeat）

============================================================================
连接管理
============================================================================

  - HpAgent 在 ws://0.0.0.0:8082 上启动 WebSocket 服务
  - NapCat 客户端主动连接到此地址
  - 支持多个客户端同时连接（_connected_clients 集合管理）
  - 发送消息时向所有已连接客户端广播

============================================================================
消息发送限制
============================================================================

  - 发送间隔: 2 秒（防止 QQ 平台风控）
  - 支持私聊（send_private_msg）和群聊（send_group_msg）
  - detail_type 必须在 metadata 中指定（"private" 或 "group"）
"""
import asyncio
import json
import logging
from typing import Any, Optional, Callable, Awaitable

import websockets

from sandbox.channels.base import BaseChannel
from common.types import ChannelType, UnifiedMessage

logger = logging.getLogger("HpAgent")


class NapCatChannel(BaseChannel):
    """NapCat 协议通道 —— 通过 WebSocket 接收 NapCat 客户端的消息，
    并将其标准化为统一消息格式。

    架构:
      NapCat 客户端 ──WebSocket──→ HpAgent (ws://0.0.0.0:8082)
        ↓ normalize_message() → UnifiedMessage
        ↓ callback → Worker.handle_message()
        ↓ agentic loop 处理
        ↓ ChannelRouter.send() → send_message()
        ↓ WebSocket → NapCat 客户端 → QQ 平台

    Attributes:
        _host: WebSocket 服务监听地址。
        _port: WebSocket 服务监听端口（默认 8082）。
        _server_task: 服务端 asyncio Task 引用。
        _connected_clients: 当前已连接的 WebSocket 客户端集合。
    """

    def __init__(self):
        super().__init__(ChannelType.NAPCAT)
        self._host = "0.0.0.0"
        self._port = 8082
        self._server_task: Optional[asyncio.Task] = None
        self._connected_clients = set()

    async def normalize_message(self, raw_message: Any) -> Optional[UnifiedMessage]:
        """将 NapCat / OneBot v11 WebSocket 上报事件标准化为统一消息格式。

        支持 post_type: message, notice, request, meta_event。
        根据 OneBot v11 标准，post_type 分为四类：
          - message    消息事件（私聊/群聊/频道）
          - notice     通知事件（群文件上传、管理员变动、成员增减、禁言、好友添加等）
          - request    请求事件（加好友、加群/邀请入群）
          - meta_event 元事件（生命周期、心跳）

        每种事件类型提取不同的 sender_id / content / metadata 字段，
        确保下游处理逻辑可以根据 metadata 中的 post_type 和 detail_type
        进行差异化处理。

        Args:
            raw_message: 原始数据，可以是 JSON 字符串或已解析的 dict。

        Returns:
            统一格式的 UnifiedMessage 对象，其中 channel_type 统一为 NAPCAT，
            metadata 中包含 post_type、detail_type、sub_type 等原始上下文。
            对于无法识别的 post_type 返回 None。
        """
        # ── 解析原始消息为 dict ──
        if isinstance(raw_message, str):
            try:
                data = json.loads(raw_message)
            except json.JSONDecodeError:
                raise ValueError(f"NapCat protocol error: Invalid JSON message: {raw_message}")
        elif isinstance(raw_message, dict):
            data = raw_message
        else:
            raise ValueError(f"Unsupported message type: {type(raw_message)}")

        post_type = data.get("post_type")
        if not post_type:
            return

        # metadata 用于保留原始事件上下文，下游可根据 post_type 做分支处理
        metadata: dict[str, Any] = {
            "post_type": post_type,
            "detail_type": None,
            "sub_type": None,
        }

        sender_id = ""
        content = ""

        # ═══════════════════════════════════════════════════════════════
        # 消息事件（message）—— 核心交互事件
        # ═══════════════════════════════════════════════════════════════
        if post_type == "message":
            message_type = data.get("message_type", "")
            metadata["detail_type"] = message_type
            metadata["sub_type"] = data.get("sub_type", "")

            sender_id = str(data.get("sender", {}).get("user_id", ""))
            content = data.get("raw_message", "") or data.get("message", "")

            # 群聊消息: 额外保存 group_id 用于回复路由
            if message_type == "group":
                group_id = data.get("group_id")
                if group_id is not None:
                    metadata["group_id"] = group_id
            # 频道消息: 额外保存 guild_id / channel_id
            elif message_type == "guild":
                metadata["guild_id"] = data.get("guild_id")
                metadata["channel_id"] = data.get("channel_id")

        # ═══════════════════════════════════════════════════════════════
        # 通知事件（notice）—— 群管理 / 好友变动等
        # ═══════════════════════════════════════════════════════════════
        elif post_type == "notice":
            notice_type = data.get("notice_type", "")
            metadata["detail_type"] = notice_type
            metadata["sub_type"] = data.get("sub_type", "")

            group_id = data.get("group_id")
            if group_id is not None:
                metadata["group_id"] = group_id

            # 不同通知类型的 sender_id 来源不同
            if notice_type in ("group_upload",):
                sender_id = str(data.get("user_id", ""))
            elif notice_type in ("group_admin",):
                sender_id = str(data.get("user_id", ""))
            elif notice_type in ("group_decrease", "group_increase", "group_ban"):
                sender_id = str(data.get("operator_id", "") or data.get("user_id", ""))
            elif notice_type == "group_recall":
                sender_id = str(data.get("operator_id", ""))
                msg_id = data.get("message_id")
                if msg_id is not None:
                    content = f"msg_id:{msg_id}"
            elif notice_type == "poke":
                sender_id = str(data.get("user_id", ""))
                content = str(data.get("target_id", ""))
            elif notice_type in ("friend_add", "friend_recall", "client_status",
                                 "honor", "lucky_king", "group_card", "offline_file"):
                sender_id = str(data.get("user_id", ""))
            else:
                sender_id = str(data.get("user_id", ""))

        # ═══════════════════════════════════════════════════════════════
        # 请求事件（request）—— 加好友 / 加群申请
        # ═══════════════════════════════════════════════════════════════
        elif post_type == "request":
            request_type = data.get("request_type", "")
            metadata["detail_type"] = request_type
            metadata["sub_type"] = data.get("sub_type", "")

            sender_id = str(data.get("user_id", ""))
            content = data.get("comment", "")

            if request_type == "group":
                group_id = data.get("group_id")
                if group_id is not None:
                    metadata["group_id"] = group_id

            # flag 用于处理请求（同意/拒绝）
            metadata["flag"] = data.get("flag", "")

        # ═══════════════════════════════════════════════════════════════
        # 元事件（meta_event）—— 生命周期 / 心跳
        # ═══════════════════════════════════════════════════════════════
        elif post_type == "meta_event":
            meta_event_type = data.get("meta_event_type", "")
            metadata["detail_type"] = meta_event_type
            metadata["sub_type"] = data.get("sub_type", "")

            sender_id = str(data.get("self_id", ""))
            content = ""

            # 心跳事件无需特殊处理，仅记录日志
            if meta_event_type == "heartbeat":
                pass

        # ═══════════════════════════════════════════════════════════════
        # 未知事件类型 —— 记录警告但继续处理
        # ═══════════════════════════════════════════════════════════════
        else:
            metadata["detail_type"] = post_type
            logger.warning(f"Unknown post_type: {post_type}, treating as generic event")

        return UnifiedMessage(
            sender_id=sender_id,
            content=content,
            channel_type=ChannelType.NAPCAT,
            metadata=metadata,
        )

    async def send_message(self, message: UnifiedMessage) -> bool:
        """向已连接的 NapCat 客户端发送消息。

        根据 metadata 中的 detail_type 选择 API:
          - "private" → send_private_msg（私聊）
          - "group"   → send_group_msg（群聊）

        消息向所有已连接客户端广播。
        发送间隔默认 2 秒（QQ 平台风控要求）。

        Args:
            message: 统一消息对象，metadata 中需包含 detail_type（private/group）
                    以及对应的 user_id (私聊) 或 group_id (群聊)。

        Returns:
            发送是否成功。
        """
        if not self._connected_clients:
            logger.warning("No connected NapCat clients available")
            return False

        # 从标准化元数据中提取目标类型
        detail_type = message.metadata.get("detail_type")
        if detail_type not in ("private", "group"):
            logger.error(f"Invalid or missing detail_type: {detail_type}")
            return False

        # 根据目标类型构造 OneBot API 请求
        if detail_type == "group":
            group_id = message.metadata.get("group_id")
            if not group_id:
                logger.error("Group message target but group_id not found in metadata")
                return False
            payload = {
                "action": "send_group_msg",
                "params": {
                    "group_id": group_id,
                    "message": message.content,
                },
            }
        else:  # private
            user_id = message.sender_id
            if not user_id:
                logger.error("Private message target but sender_id is empty")
                return False
            payload = {
                "action": "send_private_msg",
                "params": {
                    "user_id": user_id,
                    "message": message.content,
                },
            }

        # 广播到所有已连接客户端（含发送间隔防风控）
        try:
            send_tasks = [
                client.send(json.dumps(payload))
                for client in self._connected_clients
            ]
            await asyncio.gather(*send_tasks)
            logger.info(f"Sent message to NapCat clients: {message.content}")
            return True
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            return False

    # ── WebSocket 连接处理 ──

    async def _handle_message(self, websocket, raw_message: str):
        """处理接收到的 WebSocket 消息。

        流程:
          1. 标准化消息（normalize_message）
          2. 调用回调函数（传递给 Worker 的消息处理器）
          3. 对于普通消息（post_type=message），记录日志

        Args:
            websocket: WebSocket 连接对象（用于发送回复）。
            raw_message: 原始消息字符串。
        """
        if not raw_message.strip():
            return
        try:
            channel_message = await self.normalize_message(raw_message)
            if not channel_message:
                return

            # 调用 Worker 注入的消息回调 → 启动或 signal Workflow
            if self._callback:
                await self._callback(channel_message)

            # 普通消息事件: 记录详细日志
            if channel_message.metadata.get("post_type") == "message":
                message_type = channel_message.metadata.get("detail_type", "")
                sender_id = channel_message.sender_id
                content = channel_message.content

                logger.info(
                    f"Received {message_type} message from {sender_id}: \n{content}\n\n"
                )

        except json.JSONDecodeError:
            logger.warning(f"Received invalid JSON message: {raw_message}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _main_logic(self, websocket):
        """WebSocket 连接的主逻辑 —— 管理单个客户端连接的生命周期。

        流程:
          1. 客户端连接 → 加入 _connected_clients 连接池
          2. 持续接收消息 → 逐条调用 _handle_message 处理
          3. 客户端断开 → 从连接池移除

        Args:
            websocket: WebSocket 连接对象。
        """
        client_addr = websocket.remote_address
        logger.info(f"NapCat client connected: {client_addr}")
        self._connected_clients.add(websocket)

        try:
            async for message in websocket:
                await self._handle_message(websocket, message)
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning(
                f"NapCat client connection closed unexpectedly: {e.code} - {e.reason}"
            )
        except Exception as e:
            logger.error(f"Unexpected error in NapCat connection handler: {e}")
        finally:
            logger.info(f"NapCat client disconnected: {client_addr}")
            self._connected_clients.discard(websocket)

    # ── 生命周期管理 ──

    async def start_monitor(
        self, callback: Optional[Callable[[UnifiedMessage], Awaitable[None]]] = None
    ) -> bool:
        """启动 NapCat 消息监控 —— 在 ws://0.0.0.0:8082 上启动 WebSocket 服务器。

        通过 asyncio.create_task 在后台运行，不阻塞调用方。
        NapCat 客户端配置正向 WebSocket 连接到 ws://<host>:8082 即可。

        Args:
            callback: 消息回调函数，收到消息时被调用（通常为 Worker.handle_message）。

        Returns:
            是否启动成功。
        """
        self._monitoring = True
        self._callback = callback

        async def start_server():
            logger.info(
                f"HpAgent NapCat channel server starting on "
                f"ws://{self._host}:{self._port}"
            )
            async with websockets.serve(
                lambda ws, path=None: self._main_logic(ws),
                self._host,
                self._port,
            ):
                # asyncio.Future() 永不完成 → 服务持续运行
                await asyncio.Future()

        self._server_task = asyncio.create_task(start_server())
        return True

    async def stop_monitor(self) -> bool:
        """停止 NapCat 消息监控 —— 关闭 WebSocket 服务器并断开所有客户端。

        流程:
          1. 取消 server_task
          2. 清空已连接客户端集合
          3. 重置回调

        Returns:
            是否停止成功。
        """
        self._monitoring = False
        self._callback = None

        if self._server_task:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None

        self._connected_clients.clear()
        logger.info("NapCat channel stopped monitoring")
        return True
