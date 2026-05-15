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
import time
from pathlib import Path
from typing import Dict, List, Optional

from common.types import Event
from .models import Session, SessionStatus

logger = logging.getLogger("HpAgent.SessionStore")


class SessionStore:
    """会话存储 —— Redis 热数据 + Hindsight 长期记忆 + 本地文件备份。

    Usage::

        store = SessionStore(redis_cache, hindsight_client, backup_dir=Path("data/sessions"))
        await store.create_session("agent-u1", "u1", "napcat")
        await store.append_events("agent-u1", user_event, model_event)
        events = await store.get_events("agent-u1", limit=40)
        await store.retain_memories(events, "u1", "agent-u1")  # also backs up to file
    """

    _EVENTS_KEY = "session:{}:events"
    _META_KEY = "session:{}:meta"
    _ACTIVE_KEY = "account:{}:active"
    _DEFAULT_TTL = 86400  # 24h

    def __init__(self, redis_cache=None, hindsight_client=None, backup_dir: Path | str | None = None):
        """
        Args:
            redis_cache: RedisCache 实例（None 时使用内存 dict 回退）。
            hindsight_client: HindsightClient 实例（None 时记忆功能不可用）。
            backup_dir: 本地文件备份目录（None 时不备份）。
        """
        self._cache = redis_cache
        self._redis = redis_cache.redis if redis_cache else None
        self._hindsight = hindsight_client
        self._backup_dir = Path(backup_dir) if backup_dir else None

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
        else:
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
            return None
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
            return None
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
        else:
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
        else:
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
                return 0

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
                return []

        lst = self._mem_events.get(session_id, [])
        start = max(0, len(lst) - limit - offset)
        end = len(lst) - offset if offset else len(lst)
        return lst[start:end]

    # ═══════════════════════════════════════════════════════════════════════════
    # Memory (delegates to Hindsight) + File backup
    # ═══════════════════════════════════════════════════════════════════════════

    async def recall_memories(
        self,
        query: str,
        account_id: str,
        session_id: str = "",
        top_n: int = 5,
    ):
        """召回长期记忆。返回 (items: list[MemoryItem], formatted: str)。"""
        if not self._hindsight:
            return [], ""
        try:
            items = await self._hindsight.recall(query, account_id, session_id, top_n)
            formatted = await self._hindsight.recall_formatted(
                query, account_id, session_id, top_n
            )
            return items, formatted
        except Exception as e:
            logger.warning("DEGRADATION: Hindsight recall failed (%s) → memory disabled for this turn", e)
            return [], ""

    async def retain_memories(
        self,
        events: list[dict],
        account_id: str,
        session_id: str,
    ) -> int:
        """从对话事件中提取长期记忆，同时备份到本地文件。

        1. Hindsight retain（主存储，pgvector）
        2. 本地 JSONL 备份（防灾，追加写入）

        Returns:
            已存储的记忆数量。
        """
        count = 0
        if self._hindsight:
            try:
                count = await self._hindsight.retain(events, account_id, session_id)
            except Exception as e:
                logger.warning("DEGRADATION: Hindsight retain failed (%s) → events saved to file backup only", e)

        # 同步备份到本地文件（与 Hindsight 同时写入）
        await self._backup_to_file(session_id, None, events)
        return count

    async def reflect(self, account_id: str) -> int:
        """触发深度记忆推理（委托给 Hindsight）。"""
        if not self._hindsight:
            return 0
        try:
            return await self._hindsight.reflect(account_id)
        except Exception as e:
            logger.warning("DEGRADATION: Hindsight reflect failed (%s) → summary unavailable", e)
            return 0

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
        """将事件/会话写入本地 JSONL 备份文件。

        每行一个 JSON 对象，格式:
          {"type": "retain"|"archive", "session_id": "...", "timestamp": ...,
           "session_meta": {...} | null, "events": [...] | null}

        Args:
            session_id: 会话 ID。
            session: 会话元数据（archive 时传入）。
            events: 事件列表（retain 时是 dict 列表，archive 时是 Event 列表）。
            is_final: 是否为归档时的最终备份。
        """
        if self._backup_dir is None:
            return

        try:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            logger.warning("Failed to create backup dir: %s", self._backup_dir)
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

        filepath = self._backup_dir / f"{session_id}.jsonl"
        line = json.dumps(record, ensure_ascii=False) + "\n"

        try:
            await asyncio.to_thread(_append_line, filepath, line)
            logger.debug("Backup written: %s (%s)", filepath, record["type"])
        except Exception as e:
            logger.warning("File backup failed for %s: %s", session_id, e)


def _append_line(filepath: Path, line: str) -> None:
    """同步追加一行到文件（在 asyncio.to_thread 中执行）。"""
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line)
