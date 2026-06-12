"""
GroupContextStore —— 群聊短期上下文缓存，基于 Redis。

维护每个群的滑动窗口消息缓存 + 订阅者引用计数，
实现类似 weak_ptr 的生命周期管理：

  - 所有群消息写入 Redis List（LPUSH + LTRIM 维持窗口大小）
  - 当 session 开始使用群上下文时 subscribe
  - session 归档/过期时 unsubscribe
  - 所有订阅者退订后：缩 TTL 至 1h 悬空期（给新 session 恢复上下文的机会）
  - 悬空期内有新消息或新订阅者时恢复完整 TTL
  - TTL 自然过期兜底

Redis Key 设计::

  group:conv:{group_id}:messages     → List (滑动窗口)
  group:conv:{group_id}:subscribers  → Set  (活跃 session_id)
  group:conv:{group_id}:meta         → Hash (created_at, last_msg_at, message_count)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger("HpAgent.GroupContext")


# ── 常量 ────────────────────────────────────────────────────────────────────

_DEFAULT_WINDOW_SIZE = 80
_DEFAULT_TTL_SECONDS = 86400          # 24h
_DANGLING_TTL_SECONDS = 3600          # 无人订阅后保留 1h
_META_TTL_SECONDS = 86400 * 7         # meta 保留 7 天


class GroupContextStore:
    """群聊短期上下文管理器。

    用法::

        store = GroupContextStore(redis_client, window_size=80)

        # 1. 所有群消息写入
        await store.append("群号123", "小刚", "123456", "下周爬山去不去？")

        # 2. 触发回复时取窗口内容注入 prompt
        context_text = await store.get_window("群号123")

        # 3. session 开始使用群上下文
        await store.subscribe("群号123", "session-xxx")

        # 4. session 归档时退订，无人引用则清理
        await store.unsubscribe("群号123", "session-xxx")

        # 5. 获取快照用于归档
        snapshot = await store.snapshot("群号123")
    """

    def __init__(
        self,
        redis,
        window_size: int = _DEFAULT_WINDOW_SIZE,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        density_threshold: float = 2.0,
    ) -> None:
        """
        Args:
            redis: redis.asyncio.Redis 客户端实例（已连接）。
            window_size: 滑动窗口保留消息数量。
            ttl_seconds: 有订阅者时的 TTL（秒）。每次 append/subscribe 续期。
            density_threshold: 低密度阈值（msg/min），低于此值允许进度提示。
        """
        self._r = redis
        self._window = window_size
        self._ttl = ttl_seconds
        self.density_threshold = density_threshold

    # ── 消息写入 ────────────────────────────────────────────────────────────

    async def append(
        self,
        group_id: str,
        sender_name: str,
        sender_id: str,
        content: str,
        msg_id: str = "",
        timestamp: str = "",
    ) -> None:
        """将一条群消息追加到该群的滑动窗口。

        Args:
            group_id: 群号。
            sender_name: 发送者昵称/群名片。
            sender_id: 发送者 QQ 号。
            content: 消息文本内容。
            msg_id: OneBot message_id（去重用，可选）。
            timestamp: ISO 时间戳（可选，默认当前时间）。
        """
        ts = timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        msg = json.dumps({
            "sender": sender_name,
            "sender_id": sender_id,
            "content": content,
            "msg_id": str(msg_id) if msg_id else "",
            "ts": ts,
        }, ensure_ascii=False)

        pipe = self._r.pipeline()
        try:
            # LPUSH 新消息到左侧（最新的在最前面）
            list_key = self._k_messages(group_id)
            pipe.lpush(list_key, msg)
            pipe.ltrim(list_key, 0, self._window - 1)

            # 更新 meta
            meta_key = self._k_meta(group_id)
            pipe.hset(meta_key, "last_msg_at", ts)
            pipe.hincrby(meta_key, "message_count", 1)

            # TTL 在每次 append 时续期（有新消息就保持完整 TTL）
            subs_key = self._k_subscribers(group_id)
            pipe.expire(list_key, self._ttl)
            pipe.expire(subs_key, self._ttl)
            pipe.expire(meta_key, _META_TTL_SECONDS)

            await pipe.execute()
        except Exception:
            logger.exception("Failed to append group context for group %s", group_id)

    # ── 窗口读取 ────────────────────────────────────────────────────────────

    async def get_window(self, group_id: str) -> str:
        """获取群的滑动窗口消息，格式化为 prompt 可用的纯文本。

        Returns:
            格式化后的群聊上下文文本，按时间正序排列。
            例::
                小刚: 下周去爬山有人一起吗？
                小红: 好啊，周六天气不错。
                小刚: 带什么装备？
        """
        try:
            list_key = self._k_messages(group_id)
            raw = await self._r.lrange(list_key, 0, -1)
            if not raw:
                return ""

            messages = []
            for r in raw:
                try:
                    m = json.loads(r)
                    messages.append(m)
                except json.JSONDecodeError:
                    continue

            # 按时间正序排列（Redis list 中最新在左侧，反转为最早在前）
            messages.reverse()

            lines = []
            for m in messages:
                sender = m.get("sender", "unknown")
                content = m.get("content", "")
                # 跳过空消息和过长消息
                if not content.strip():
                    continue
                display = content[:200] + ("..." if len(content) > 200 else "")
                lines.append(f"{sender}: {display}")

            return "\n".join(lines)
        except Exception:
            logger.exception("Failed to read group context for group %s", group_id)
            return ""

    async def get_window_json(self, group_id: str) -> list[dict]:
        """获取群的滑动窗口原始消息列表（用于归档快照）。

        Returns:
            消息列表，按时间正序排列。
        """
        try:
            list_key = self._k_messages(group_id)
            raw = await self._r.lrange(list_key, 0, -1)
            messages = []
            for r in raw:
                try:
                    messages.append(json.loads(r))
                except json.JSONDecodeError:
                    continue
            messages.reverse()
            return messages
        except Exception:
            return []

    # ── 订阅者管理 ──────────────────────────────────────────────────────────

    async def subscribe(self, group_id: str, session_id: str) -> None:
        """标记 session 正在使用该群的上下文。

        幂等操作。存在性也用于防止 Redis TTL 过期清理：
        只要还有 subscriber，群对话数据就应该保持存活。
        """
        try:
            subs_key = self._k_subscribers(group_id)
            pipe = self._r.pipeline()
            pipe.sadd(subs_key, session_id)
            # 有新订阅者 → 恢复完整 TTL
            pipe.expire(self._k_messages(group_id), self._ttl)
            pipe.expire(subs_key, self._ttl)
            await pipe.execute()
            logger.debug("Group context subscribed: group=%s session=%s", group_id, session_id)
        except Exception:
            logger.exception("Failed to subscribe group %s session %s", group_id, session_id)

    async def unsubscribe(self, group_id: str, session_id: str) -> int:
        """标记 session 不再使用该群的上下文。

        当订阅者数量归零时，主动清理该群的缓存数据。

        Returns:
            剩余订阅者数量。
        """
        try:
            subs_key = self._k_subscribers(group_id)
            pipe = self._r.pipeline()
            pipe.srem(subs_key, session_id)
            pipe.scard(subs_key)
            results = await pipe.execute()
            remaining = results[1]  # scard 的返回值

            if remaining == 0:
                # 无订阅者 → 缩 TTL 到悬空期（1h），给新 session 恢复的机会
                await self._shrink_ttl_to_dangling(group_id)
                logger.info("Group context dangling: group=%s (zero subscribers, TTL→1h)", group_id)
            else:
                logger.debug(
                    "Group context unsubscribed: group=%s session=%s remaining=%d",
                    group_id, session_id, remaining,
                )

            return remaining
        except Exception:
            logger.exception("Failed to unsubscribe group %s session %s", group_id, session_id)
            return -1

    async def subscriber_count(self, group_id: str) -> int:
        """查询当前该群的活跃订阅者数量。"""
        try:
            return await self._r.scard(self._k_subscribers(group_id))
        except Exception:
            return 0

    # ── 密度感知 ────────────────────────────────────────────────────────────

    _DENSITY_WINDOW_MINUTES = 5   # 统计最近 N 分钟的消息

    async def get_density(self, group_id: str, window_minutes: float = 0) -> float:
        """计算群聊最近 N 分钟的消息密度（msg/min）。

        基于滑动窗口中消息的时间戳计算。
        密度低 → 发送进度提示不会刷屏；密度高 → 保持沉默。

        Args:
            group_id: 群号。
            window_minutes: 统计窗口（分钟）。0 用默认值 _DENSITY_WINDOW_MINUTES。

        Returns:
            float: 消息/分钟。Redis 异常或无消息时返回 0。
        """
        if window_minutes <= 0:
            window_minutes = self._DENSITY_WINDOW_MINUTES

        try:
            messages = await self.get_window_json(group_id)
            if not messages:
                return 0.0

            now = datetime.now(timezone.utc)
            cutoff = now.timestamp() - (window_minutes * 60)
            recent = 0
            for m in messages:
                ts_str = m.get("ts", "")
                if not ts_str:
                    continue
                try:
                    # ISO 8601: "2026-06-10T15:18:25Z"
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    if ts.timestamp() >= cutoff:
                        recent += 1
                except (ValueError, TypeError):
                    continue

            return recent / window_minutes
        except Exception:
            logger.debug("Failed to compute density for group %s", group_id, exc_info=True)
            return 0.0

    # ── 快照与归档 ──────────────────────────────────────────────────────────

    async def snapshot(self, group_id: str) -> dict[str, Any]:
        """获取群的当前上下文快照，供 session 归档时写入 meta.yaml。

        Returns:
            {
                "group_id": "群号123",
                "message_count": 47,
                "snapshot": "小刚: 下周去爬山有人一起吗？\n小红: 好啊...",
                "subscriber_count": 2,
            }
        """
        try:
            messages = await self.get_window_json(group_id)
            text = await self.get_window(group_id)

            meta_key = self._k_meta(group_id)
            meta = await self._r.hgetall(meta_key)
            stored_count = int(meta.get(b"message_count", len(messages)))
            created_at = meta.get(b"created_at", b"").decode()

            subs = await self.subscriber_count(group_id)

            return {
                "group_id": group_id,
                "message_count": stored_count,
                "window_snapshot": text,
                "subscriber_count": subs,
                "created_at": created_at,
            }
        except Exception:
            logger.exception("Failed to snapshot group %s", group_id)
            return {"group_id": group_id, "message_count": 0, "window_snapshot": ""}

    # ── 内部方法 ────────────────────────────────────────────────────────────

    @staticmethod
    def _k_messages(group_id: str) -> str:
        return f"group:conv:{group_id}:messages"

    @staticmethod
    def _k_subscribers(group_id: str) -> str:
        return f"group:conv:{group_id}:subscribers"

    @staticmethod
    def _k_meta(group_id: str) -> str:
        return f"group:conv:{group_id}:meta"

    async def _shrink_ttl_to_dangling(self, group_id: str) -> None:
        """最后一个订阅者退订后将 TTL 缩至悬空期（1h）。

        不主动 DEL：给新 session 在 1h 内启动并 subscribe 时恢复完整 TTL 的机会。
        悬空期内无新消息也无新订阅 → Redis TTL 自然过期清理。
        """
        pipe = self._r.pipeline()
        pipe.expire(self._k_messages(group_id), _DANGLING_TTL_SECONDS)
        pipe.expire(self._k_subscribers(group_id), _DANGLING_TTL_SECONDS)
        await pipe.execute()
