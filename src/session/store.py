"""
SessionStore —— 会话存储层。

只负责存储，不包含操作逻辑。内部封装:
  - Redis: 活跃会话的事件流 + 元数据（高频读写）
  - HindsightClient: 长期记忆召回/提取（低频）
  - WAL: 本地预写日志（事件真相源，append-only）
  - 内存 dict: Redis 不可用时的开发回退

Redis Key 设计:
  session:{session_id}:events       → List  事件流（RPUSH 追加 / LRANGE 读取）
  session:{session_id}:meta         → Hash  会话元数据
  account:{account_id}:active       → String 指向活跃 session_id

WAL 路径: {backup_dir}/{session_id}.wal
归档路径: {workspace}/{account_id}/sessions/{session_id}/history.jsonl + meta.yaml

归档时序（防丢数据）:
  1. SessionStore.archive() — 返回全部事件，标记会话完成
  2. write_history_jsonl    — 从 events 写入工作区归档快照（永久真相源）
  3. delete_wal             — 删除 WAL 文件（活跃期使命已完成）
  4. generate_summary + update_meta — fast 模型摘要

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

        store = SessionStore(redis_cache, hindsight_client, backup_dir=Path(".data/active-sessions"))
        await store.create_session("agent-u1", "u1", "napcat")
        await store.append_events("agent-u1", user_event, model_event)
        events = await store.get_events("agent-u1", limit=40)
        await store.retain_memories(events, "u1", "agent-u1")  # also backs up to file
    """

    _EVENTS_KEY = "session:{}:events"
    _META_KEY = "session:{}:meta"
    _ACTIVE_KEY = "account:{}:active"
    _DEFAULT_TTL = 86400  # 24h

    def __init__(self, redis_cache=None, hindsight_client: Optional[HindsightClient] = None, file_store=None,
                 *, wal_enabled: bool = True, checkpoint_enabled: bool = True):
        """
        Args:
            redis_cache: RedisCache 实例（None 时使用内存 dict 回退）。
            hindsight_client: HindsightClient 实例（None 时记忆功能不可用）。
            file_store: LocalFileStore 实例（None 时不备份到文件）。
            wal_enabled: 启用 WAL 预写日志（file_store 存在时才生效）。
            checkpoint_enabled: 启用中间检查点。
        """
        self._cache = redis_cache
        self._redis = redis_cache.redis if redis_cache else None
        self._hindsight = hindsight_client
        self._file_store = file_store
        self._wal_enabled = wal_enabled
        self._checkpoint_enabled = checkpoint_enabled

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

        # 跨会话上下文继承（P2-2）—— 注入上一 session 摘要
        if metadata is None or metadata.get("inherit_context", True):
            prev_summary = await self._get_previous_session_summary(account_id)
            if prev_summary:
                inherit_event = Event(
                    session_id=session_id,
                    event_type=EventType.CONTEXT_INHERIT,
                    content={"summary": prev_summary},
                )
                await self.append_events(session_id, inherit_event)

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

    async def archive(self, session_id: str) -> list[dict]:
        """归档会话：标记状态 + 返回全部事件。

        不再在此方法内写文件——仅清理 Redis 活跃指针。
        事件导出和 meta 摘要由上层（HarnessRunner.archive_session）编排。

        Returns:
            全部事件的 to_dict() 列表，供上层写入 history.jsonl。
        """
        await self.update_status(session_id, SessionStatus.COMPLETED)
        session = await self.get_session(session_id)
        if session is None:
            return []

        # 清理 Redis 活跃指针
        if self._cache:
            try:
                await self._cache.delete(self._ACTIVE_KEY.format(session.account_id))
            except Exception as e:
                logger.warning("DEGRADATION: Redis archive delete failed (%s) → falling back to memory", e)
        self._mem_active.pop(session.account_id, None)

        # 读取全部事件
        events = await self.get_events(session_id, limit=10000)
        event_dicts = [e.to_dict() for e in events]

        logger.info("Session archived: %s (%d events, WAL retained)", session_id, len(event_dicts))
        return event_dicts

    # ═══════════════════════════════════════════════════════════════════════════
    # WAL (Write-Ahead Log) —— 真相源，先于 Redis 写入
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _wal_path(session_id: str) -> str:
        """返回 WAL 文件名。"""
        return f"{session_id}.wal"

    async def _append_to_wal(self, session_id: str, *events: Event) -> None:
        """将事件追加写入 WAL 文件。

        WAL 是真相源，在 Redis 之前写入。每行一个 JSON 事件。
        file_store 为空或 wal_enabled=False 时跳过。
        """
        if not self._wal_enabled or self._file_store is None:
            return
        try:
            for event in events:
                line = json.dumps(event.to_dict(), ensure_ascii=False) + "\n"
                await self._file_store.append_line(self._wal_path(session_id), line)
        except Exception as e:
            logger.warning("WAL write failed for %s: %s", session_id, e)

    async def _replay_wal(self, session_id: str) -> List[Event]:
        """从 WAL 文件回放所有事件。

        Redis 未命中或不可用时调用。按写入顺序返回事件列表。
        """
        if self._file_store is None:
            return []
        try:
            content = await self._file_store.read(self._wal_path(session_id))
            if not content:
                return []
        except Exception:
            return []  # WAL 文件不存在

        events: List[Event] = []
        for line in content.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(Event.from_dict(json.loads(line)))
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("WAL parse error for %s: %s", session_id, e)
        return events

    # ═══════════════════════════════════════════════════════════════════════════
    # Events (conversation history)
    # ═══════════════════════════════════════════════════════════════════════════

    async def append_events(self, session_id: str, *events: Event) -> int:
        """追加事件到会话流。

        写入顺序（由可靠到不可靠）:
          1. WAL 文件（本地磁盘，真相源）
          2. Redis（热缓存）
          3. 内存 dict（开发回退）
        """
        if not events:
            return 0

        # Step 1: WAL（真相源，先写）
        await self._append_to_wal(session_id, *events)

        # Step 2: Redis 缓存
        if self._redis:
            try:
                key = self._EVENTS_KEY.format(session_id)
                data = [json.dumps(e.to_dict(), ensure_ascii=False) for e in events]
                count = await self._redis.rpush(key, *data)
                await self._redis.expire(key, self._DEFAULT_TTL)
                return count
            except Exception as e:
                logger.warning("DEGRADATION: Redis rpush failed (%s) → events in WAL only", e)

        # Step 3: 内存回退
        lst = self._mem_events.setdefault(session_id, [])
        lst.extend(events)
        return len(events)

    async def get_events(
        self,
        session_id: str,
        limit: int = 40,
        offset: int = 0,
    ) -> List[Event]:
        """获取最近 N 条事件（按时间升序）。

        读取路径: Redis → WAL 回退 → 内存回退。
        """
        # Path A: Redis 缓存
        if self._redis:
            try:
                key = self._EVENTS_KEY.format(session_id)
                raw = await self._redis.lrange(key, -(limit + offset), -1)
                if raw:
                    if offset:
                        raw = raw[:-offset] if offset < len(raw) else []
                    return [Event.from_dict(json.loads(r)) for r in raw]
            except Exception as e:
                logger.warning("DEGRADATION: Redis lrange failed (%s) → WAL fallback", e)

        # Path B: WAL 回放（Redis 无数据或不可用时）
        wal_events = await self._replay_wal(session_id)
        if wal_events:
            start = max(0, len(wal_events) - limit - offset)
            end = len(wal_events) - offset if offset else len(wal_events)
            return wal_events[start:end]

        # Path C: 内存回退
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
        items: list = []
        formatted: str = ""
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
            formatted = await self._hindsight.recall_formatted(items=items)
            return items, formatted
        except Exception as e:
            error = str(e)
            logger.warning("DEGRADATION: Hindsight recall failed (%s) → memory disabled for this turn", e)
            return [], ""
        finally:
            elapsed_ms = (time.monotonic() - t0) * 1000
            # 记录 recall 实际返回的文本（截断以防过大）
            recall_items_snapshot = []
            if items:
                for item in items:
                    recall_items_snapshot.append({
                        "content": item.content[:300],
                        "relevance": item.relevance,
                        "memory_type": item.memory_type,
                        "source": item.source_session_id,
                    })
            await self._record_memory_event(session_id, EventType.MEMORY_RECALL, {
                "query": clean_query[:200],
                "items_count": items_count,
                "items": recall_items_snapshot,
                "formatted": formatted[:500] if formatted else "",
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
        """从对话事件中提取长期记忆。

        1. Hindsight retain（主存储，pgvector）
        2. MEMORY_RETAIN 事件写入会话流（审计）

        注意: 不在此处写 JSONL 备份。事件持久化由 WAL（实时）和
        archive()（归档）负责，职责分离。

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
                logger.warning("DEGRADATION: Hindsight retain failed (%s) → events preserved in WAL/checkpoint", e)

        elapsed_ms = (time.monotonic() - t0) * 1000
        # 截取本轮对话文本片段用于审计回放
        turn_snippet = "\n".join(
            f"[{e.get('role', '?')}]: {e.get('content', '')[:200]}"
            for e in (events or [])[-6:] if e.get("content")
        )
        await self._record_memory_event(session_id, EventType.MEMORY_RETAIN, {
            "events_count": len(events),
            "items_stored": count,
            "turn_snippet": turn_snippet[:2000],
            "latency_ms": round(elapsed_ms, 1),
            "document_id": f"session:{session_id}",
            "error": error,
        })
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

    async def delete_wal(self, session_id: str) -> None:
        """删除 WAL 文件 —— 归档至 history.jsonl 后调用。

        WAL 是活跃期真相源，归档后 history.jsonl 接替为永久真相源，
        此时 WAL 已完成使命，直接删除不留空文件。
        """
        if not self._wal_enabled or self._file_store is None:
            return
        try:
            await self._file_store.delete(self._wal_path(session_id))
            logger.debug("WAL deleted after archive: %s", session_id)
        except Exception as e:
            logger.warning("WAL delete failed for %s: %s", session_id, e)

    # ═══════════════════════════════════════════════════════════════════════════
    # Checkpoint (P2-1) —— 中间检查点，用于崩溃恢复
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _checkpoint_path(session_id: str, turn_number: int) -> str:
        """返回检查点文件路径。"""
        return f"{session_id}.ckpt_turn{turn_number}.json"

    async def write_checkpoint(
        self, session_id: str, events: List[Event], in_session_summary: str = "",
    ) -> int | None:
        """将当前事件 + 会话内摘要写入检查点文件（原子写入）。

        Returns:
            检查点轮次号，失败时返回 None。
        """
        if not self._checkpoint_enabled or self._file_store is None:
            return None

        turn_events = [e for e in events if e.event_type in (
            EventType.USER_MESSAGE, EventType.MODEL_MESSAGE)]
        turn_number = len(turn_events)

        ckpt = {
            "session_id": session_id,
            "turn": turn_number,
            "timestamp": time.time(),
            "event_count": len(events),
            "in_session_summary": in_session_summary,
            "events": [e.to_dict() for e in events],
        }

        path = self._checkpoint_path(session_id, turn_number)
        try:
            self._file_store.write_atomic_sync(
                path, json.dumps(ckpt, ensure_ascii=False),
            )
            logger.info("Checkpoint written: %s turn=%d events=%d", path, turn_number, len(events))
            return turn_number
        except Exception as e:
            logger.warning("Checkpoint write failed for %s: %s", path, e)
            return None

    async def load_latest_checkpoint(self, session_id: str) -> dict | None:
        """加载该会话最新的检查点文件。

        Returns:
            {'session_id', 'turn', 'event_count', 'in_session_summary',
             'events': [...], '_events_obj': [Event, ...]}，无检查点时返回 None。
        """
        if self._file_store is None:
            return None
        try:
            files = await self._file_store.list(".", pattern="*.ckpt_turn*.json")
            prefix = f"{session_id}.ckpt_turn"
            matching = [f for f in files if f.startswith(prefix)]
            if not matching:
                return None

            def _turn_from_name(name: str) -> int:
                try:
                    return int(name[len(prefix):-len(".json")])
                except (ValueError, IndexError):
                    return 0
            matching.sort(key=_turn_from_name, reverse=True)

            content = await self._file_store.read(matching[0])
            ckpt = json.loads(content)
            ckpt["_events_obj"] = [Event.from_dict(e) for e in ckpt.get("events", [])]
            logger.info("Checkpoint loaded: %s turn=%d", matching[0], ckpt.get("turn", 0))
            return ckpt
        except Exception as e:
            logger.warning("Checkpoint load failed for %s: %s", session_id, e)
            return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Running Summary (P1-1) —— 会话运行中的增量摘要
    # ═══════════════════════════════════════════════════════════════════════════

    async def get_in_session_summary(self, session_id: str) -> str:
        """从会话 metadata 中读取会话内摘要。"""
        session = await self.get_session(session_id)
        if session and session.metadata:
            return session.metadata.get("in_session_summary", "")
        return ""

    async def set_in_session_summary(self, session_id: str, summary: str) -> None:
        """在会话 metadata 中存储会话内摘要。"""
        session = await self.get_session(session_id)
        if session is None:
            return
        session.metadata["in_session_summary"] = summary
        if self._cache:
            try:
                await self._cache.set_json(
                    self._META_KEY.format(session_id),
                    session.to_dict(),
                    ttl=self._DEFAULT_TTL,
                )
            except Exception as e:
                logger.warning("DEGRADATION: set_in_session_summary failed (%s)", e)
        self._mem_sessions[session_id] = session

    # ═══════════════════════════════════════════════════════════════════════════
    # Cross-Session Context Inheritance (P2-2) —— 跨会话上下文继承
    # ═══════════════════════════════════════════════════════════════════════════

    async def _get_previous_session_summary(
        self, account_id: str,
    ) -> str | None:
        """查找该账号最近一个已完成 session 的摘要。

        Returns:
            格式化摘要文本，无上一会话时返回 None。
        """
        if self._file_store is None:
            return None

        sessions_dir = f"{account_id}/sessions"
        try:
            session_dirs = await self._file_store.list(sessions_dir, pattern="*")
        except Exception:
            return None

        import yaml
        completed: list[tuple[str, str, list]] = []
        for sdir in session_dirs:
            meta_path = f"{sessions_dir}/{sdir}/meta.yaml"
            try:
                content = await self._file_store.read(meta_path)
                meta = yaml.safe_load(content) or {}
                if meta.get("status") == "completed" and meta.get("task_summary"):
                    completed.append((
                        meta.get("completed_at", ""),
                        meta.get("task_summary", ""),
                        meta.get("tags", []),
                    ))
            except Exception:
                continue

        if not completed:
            return None

        completed.sort(key=lambda x: x[0], reverse=True)
        _, summary, tags = completed[0]
        if not summary:
            return None

        tag_str = f" [标签: {', '.join(tags)}]" if tags else ""
        return f"上一会话摘要: {summary}{tag_str}"


def _clean_recall_query(raw_content: str) -> str:
    """去除 @提及、CQ 码等噪声前缀，只保留核心语义内容。"""
    cleaned = raw_content
    cleaned = _CQ_CODE_RE.sub('', cleaned)
    cleaned = _AT_RE.sub('', cleaned)
    return cleaned.strip()
