"""
SessionStore —— 会话存储层。

只负责存储，不包含操作逻辑。内部封装:
  - Redis: 活跃会话的事件流 + 元数据（高频读写）
  - HindsightClient: 长期记忆召回/提取（低频）
  - 本地文件: JSONL 备份（与 Hindsight retain 同时写入，防数据丢失）
  - 内存 dict: Redis 不可用时的开发回退

Redis Key 设计:
  session:{session_id}:events       → List  事件流（RPUSH 追加 / LRANGE 读取）
  session:{session_id}:meta         → Hash  会话元数据
  account:{account_id}:active       → String 指向活跃 session_id

文件备份路径: {backup_dir}/{session_id}.jsonl

SessionStore 只被 Harness 使用，不向其他模块暴露。
所有方法在 Redis/Hindsight 不可用时静默降级，不抛异常。
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import Dict, List, Optional

from common.types import Event, EventType
from .models import Session, SessionStatus

logger = logging.getLogger("HpAgent.SessionStore")

# CQ 码及 @提及清理正则（用于 recall query 纯净化）
_CQ_CODE_RE = re.compile(r'\[CQ:[^\]]+\]')
_AT_RE = re.compile(r'@\S+')


class SessionStore:
    """会话存储 —— Redis 热数据 + Hindsight 长期记忆 + 本地文件备份。

    Usage::

        store = SessionStore(redis_cache, hindsight_client, backup_dir=Path(".data/data/sessions"))
        await store.create_session("agent-u1", "u1", "napcat")
        await store.append_events("agent-u1", user_event, model_event)
        events = await store.get_events("agent-u1", limit=40)
        await store.retain_memories(events, "u1", "agent-u1")  # also backs up to file
    """

    _EVENTS_KEY = "session:{}:events"
    _META_KEY = "session:{}:meta"
    _ACTIVE_KEY = "account:{}:active"
    _DEFAULT_TTL = 86400  # 24h

    def __init__(self, redis_cache=None, hindsight_client: Optional[HindsightClient] = None, file_store=None):
        """
        Args:
            redis_cache: RedisCache 实例（None 时使用内存 dict 回退）。
            hindsight_client: HindsightClient 实例（None 时记忆功能不可用）。
            file_store: LocalFileStore 实例（None 时不备份到文件）。
        """
        self._cache = redis_cache
        self._redis = redis_cache.redis if redis_cache else None
        self._hindsight = hindsight_client
        self._file_store = file_store

        # 内存回退（无 Redis 时使用）
        self._mem_events: Dict[str, List[Event]] = {}
        self._mem_sessions: Dict[str, Session] = {}
        self._mem_active: Dict[str, str] = {}

    # ═══════════════════════════════════════════════════════════════════════════
    # Session lifecycle
    # ═══════════════════════════════════════════════════════════════════════════

    async def create_session(
        self,
        session_id: str,
        account_id: str,
        channel_type: str = "console",
        metadata: dict | None = None,
    ) -> Session:
        """创建会话并绑定到账号。"""
        session = Session(
            session_id=session_id,
            account_id=account_id,
            status=SessionStatus.ACTIVE,
            channel_type=channel_type,
            metadata=metadata or {},
        )
        if self._cache:
            try:
                await self._cache.set_json(
                    self._META_KEY.format(session_id),
                    session.to_dict(),
                    ttl=self._DEFAULT_TTL,
                )
                await self._cache.set(
                    self._ACTIVE_KEY.format(account_id),
                    session_id.encode(),
                    ttl=self._DEFAULT_TTL,
                )
            except Exception as e:
                logger.warning("DEGRADATION: Redis write failed (%s) → falling back to memory", e)
        self._mem_sessions[session_id] = session
        self._mem_active[account_id] = session_id
        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """获取会话元数据。"""
        if self._cache:
            try:
                data = await self._cache.get_json(self._META_KEY.format(session_id))
                if data:
                    return Session.from_dict(data)
            except Exception as e:
                logger.warning("DEGRADATION: Redis read failed (%s) → falling back to memory", e)
        return self._mem_sessions.get(session_id)

    async def get_active_session_id(self, account_id: str) -> Optional[str]:
        """获取账号当前活跃的会话 ID。"""
        if self._cache:
            try:
                raw = await self._cache.get(self._ACTIVE_KEY.format(account_id))
                if raw:
                    return raw.decode() if isinstance(raw, bytes) else raw
            except Exception as e:
                logger.warning("DEGRADATION: Redis get_active_session_id failed (%s) → falling back to memory", e)
        return self._mem_active.get(account_id)

    async def update_status(self, session_id: str, status: SessionStatus) -> None:
        """更新会话状态。"""
        session = await self.get_session(session_id)
        if session is None:
            return
        session.status = status

        if self._cache:
            try:
                await self._cache.set_json(
                    self._META_KEY.format(session_id),
                    session.to_dict(),
                    ttl=self._DEFAULT_TTL,
                )
            except Exception as e:
                logger.warning("DEGRADATION: Redis update_status failed (%s) → falling back to memory", e)
        self._mem_sessions[session_id] = session

    async def archive(self, session_id: str) -> None:
        """归档会话：标记状态 + 清理活跃指针 + 写入最终备份。"""
        await self.update_status(session_id, SessionStatus.COMPLETED)
        session = await self.get_session(session_id)
        if session is None:
            return

        if self._cache:
            try:
                await self._cache.delete(self._ACTIVE_KEY.format(session.account_id))
            except Exception as e:
                logger.warning("DEGRADATION: Redis archive delete failed (%s) → falling back to memory", e)
        self._mem_active.pop(session.account_id, None)

        # 最终备份：写入会话元数据摘要
        events = await self.get_events(session_id, limit=10000)
        await self._backup_to_file(session_id, session, events, is_final=True)

    # ═══════════════════════════════════════════════════════════════════════════
    # Events (conversation history)
    # ═══════════════════════════════════════════════════════════════════════════

    async def append_events(self, session_id: str, *events: Event) -> int:
        """追加事件到会话流。"""
        if not events:
            return 0

        if self._redis:
            try:
                key = self._EVENTS_KEY.format(session_id)
                data = [json.dumps(e.to_dict(), ensure_ascii=False) for e in events]
                count = await self._redis.rpush(key, *data)
                await self._redis.expire(key, self._DEFAULT_TTL)
                return count
            except Exception as e:
                logger.warning("DEGRADATION: Redis rpush failed (%s) → events stored in memory only", e)

        lst = self._mem_events.setdefault(session_id, [])
        lst.extend(events)
        return len(events)

    async def get_events(
        self,
        session_id: str,
        limit: int = 40,
        offset: int = 0,
    ) -> List[Event]:
        """获取最近 N 条事件（按时间升序）。"""
        if self._redis:
            try:
                key = self._EVENTS_KEY.format(session_id)
                raw = await self._redis.lrange(key, -(limit + offset), -1)
                if offset:
                    raw = raw[:-offset] if offset < len(raw) else []
                return [Event.from_dict(json.loads(r)) for r in raw]
            except Exception as e:
                logger.warning("DEGRADATION: Redis lrange failed (%s) → events read from memory only", e)

        lst = self._mem_events.get(session_id, [])
        start = max(0, len(lst) - limit - offset)
        end = len(lst) - offset if offset else len(lst)
        return lst[start:end]

    # ═══════════════════════════════════════════════════════════════════════════
    # Memory (delegates to Hindsight) + File backup
    # ═══════════════════════════════════════════════════════════════════════════

    async def _record_memory_event(
        self, session_id: str, event_type: EventType, content: dict
    ) -> None:
        """记录 Hindsight 操作事件到会话事件流（审计用）。"""
        event = Event(
            session_id=session_id,
            event_type=event_type,
            content=content,
        )
        await self.append_events(session_id, event)

    async def recall_memories(
        self,
        query: str,
        account_id: str,
        session_id: str = "",
        top_n: int = 5,
        tags_match: str = "any_strict",
        query_timestamp: str = "",
        budget: str = "mid",
        group_id: str = "",
        scope: str = "",
        channel_type: str = "",
    ):
        """召回长期记忆。返回 (items: list[MemoryItem], formatted: str)。

        查询在召回前会被纯净化（去除 @提及和 CQ 码）。
        """
        if not self._hindsight:
            return [], ""
        clean_query = _clean_recall_query(query)
        t0 = time.monotonic()
        error: str = ""
        items_count = 0
        try:
            items = await self._hindsight.recall(
                clean_query, account_id, session_id, top_n,
                tags_match=tags_match,
                query_timestamp=query_timestamp,
                budget=budget,
                group_id=group_id,
                scope=scope,
                channel_type=channel_type,
            )
            items_count = len(items)
            formatted = await self._hindsight.recall_formatted(
                clean_query, account_id, session_id, top_n,
                tags_match=tags_match,
                query_timestamp=query_timestamp,
                budget=budget,
                group_id=group_id,
                scope=scope,
                channel_type=channel_type,
            )
            return items, formatted
        except Exception as e:
            error = str(e)
            logger.warning("DEGRADATION: Hindsight recall failed (%s) → memory disabled for this turn", e)
            return [], ""
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            await self._record_memory_event(session_id, EventType.MEMORY_RECALL, {
                "query": clean_query[:200],
                "items_count": items_count,
                "latency_ms": round(elapsed_ms, 1),
                "tags_match": tags_match,
                "budget": budget,
                "error": error,
            })

    async def retain_memories(
        self,
        events: list[dict],
        account_id: str,
        session_id: str,
        channel_type: str = "",
        group_id: str = "",
        sender_name: str = "",
        iso_timestamp: str = "",
        scope: str = "",
    ) -> int:
        """从对话事件中提取长期记忆，同时备份到本地文件。

        1. Hindsight retain（主存储，pgvector）
        2. 本地 JSONL 备份（防灾，追加写入）
        3. MEMORY_RETAIN 事件写入会话流（审计）

        Args:
            events:        对话事件列表 [{"role": "user", "content": "..."}, ...]。
            account_id:    账号 ID。
            session_id:    会话 ID。
            channel_type:  渠道类型。
            group_id:      群 ID（群聊时）。
            sender_name:   发送者名称。
            iso_timestamp: ISO 8601 时间戳。
            scope:         对话范围（"private" / "group"）。

        Returns:
            已存储的记忆数量。
        """
        count = 0
        error: str = ""
        t0 = time.monotonic()
        if self._hindsight:
            try:
                count = await self._hindsight.retain(
                    events, account_id, session_id,
                    async_retain=True,
                    channel_type=channel_type,
                    group_id=group_id,
                    sender_name=sender_name,
                    iso_timestamp=iso_timestamp,
                    scope=scope,
                )
            except Exception as e:
                error = str(e)
                logger.warning("DEGRADATION: Hindsight retain failed (%s) → events saved to file backup only", e)

        elapsed_ms = (time.monotonic() - t0) * 1000
        await self._record_memory_event(session_id, EventType.MEMORY_RETAIN, {
            "events_count": len(events),
            "items_stored": count,
            "latency_ms": round(elapsed_ms, 1),
            "document_id": f"session:{session_id}",
            "error": error,
        })

        # 同步备份到本地文件（与 Hindsight 同时写入）
        await self._backup_to_file(session_id, None, events)
        return count

    async def reflect(self, account_id: str) -> int:
        """触发深度记忆推理（委托给 Hindsight）。"""
        if not self._hindsight:
            return 0
        t0 = time.monotonic()
        error: str = ""
        insights = 0
        try:
            insights = await self._hindsight.reflect(account_id)
            return insights
        except Exception as e:
            error = str(e)
            logger.warning("DEGRADATION: Hindsight reflect failed (%s) → summary unavailable", e)
            return 0
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            await self._record_memory_event(
                f"reflect-{account_id}",
                EventType.MEMORY_REFLECT,
                {
                    "account_id": account_id,
                    "insights": insights,
                    "latency_ms": round(elapsed_ms, 1),
                    "error": error,
                },
            )

    # ═══════════════════════════════════════════════════════════════════════════
    # File backup (private)
    # ═══════════════════════════════════════════════════════════════════════════

    async def _backup_to_file(
        self,
        session_id: str,
        session: Session | None = None,
        events: list | None = None,
        is_final: bool = False,
    ) -> None:
        """将事件/会话写入本地 JSONL 备份文件（通过 LocalFileStore）。

        每行一个 JSON 对象，格式:
          {"type": "retain"|"archive", "session_id": "...", "timestamp": ...,
           "session_meta": {...} | null, "events": [...] | null}

        Args:
            session_id: 会话 ID。
            session: 会话元数据（archive 时传入）。
            events: 事件列表（retain 时是 dict 列表，archive 时是 Event 列表）。
            is_final: 是否为归档时的最终备份。
        """
        if self._file_store is None:
            return

        record = {
            "type": "archive" if is_final else "retain",
            "session_id": session_id,
            "timestamp": time.time(),
            "session_meta": session.to_dict() if session else None,
            "events": (
                [e.to_dict() if isinstance(e, Event) else e for e in events]
                if events else None
            ),
        }

        line = json.dumps(record, ensure_ascii=False) + "\n"

        try:
            await self._file_store.append_line(f"{session_id}.jsonl", line)
            logger.debug("Backup written: %s (%s)", session_id, record["type"])
        except Exception as e:
            logger.warning("File backup failed for %s: %s", session_id, e)


def _clean_recall_query(raw_content: str) -> str:
    """去除 @提及、CQ 码等噪声前缀，只保留核心语义内容。"""
    cleaned = raw_content
    cleaned = _CQ_CODE_RE.sub('', cleaned)
    cleaned = _AT_RE.sub('', cleaned)
    return cleaned.strip()
