"""
Session 层持久化仓库实现
支持文件存储（JSON）和 PostgreSQL 两种后端。
"""
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional
from threading import RLock

from .models import Session, EventRecord

logger = logging.getLogger(__name__)


class FileSessionRepository:
    """会话元数据仓库"""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._sessions: Dict[str, Session] = {}
        self._lock = RLock()
        if storage_path:
            self._load()

    def _load(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path) / "sessions.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    session = Session.from_dict(item)
                    self._sessions[session.session_id] = session
        except Exception:
            pass

    def _save(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        sessions_file = path / "sessions.json"
        with open(sessions_file, "w", encoding="utf-8") as f:
            json.dump(
                [s.to_dict() for s in self._sessions.values()],
                f,
                ensure_ascii=False,
                indent=2,
            )

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            self._save()

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_all(self) -> List[Session]:
        with self._lock:
            return list(self._sessions.values())


class FileEventRepository:
    """事件日志仓库"""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._events: Dict[str, List[EventRecord]] = {}
        self._counters: Dict[str, int] = {}
        self._lock = RLock()
        if storage_path:
            self._load()

    def _load(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path) / "events.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for session_id, events_list in data.items():
                    records = [EventRecord.from_dict(e) for e in events_list]
                    self._events[session_id] = records
                    if records:
                        max_index = max(e.event_index for e in records)
                        self._counters[session_id] = max_index + 1
        except Exception:
            pass

    def _save(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        events_file = path / "events.json"
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    sid: [e.to_dict() for e in events]
                    for sid, events in self._events.items()
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def append_event(self, session_id: str, event: EventRecord) -> int:
        with self._lock:
            if session_id not in self._events:
                self._events[session_id] = []
                self._counters[session_id] = 0
            index = self._counters[session_id]
            event.event_index = index
            self._events[session_id].append(event)
            self._counters[session_id] = index + 1
            self._save()
            return index

    def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[EventRecord]:
        with self._lock:
            events = self._events.get(session_id, [])
            if event_types:
                events = [e for e in events if e.event_type in event_types]
            events = events[offset:]
            if limit is not None:
                events = events[:limit]
            return events

    def truncate_events(self, session_id: str, target_index: int) -> int:
        with self._lock:
            events = self._events.get(session_id, [])
            original_len = len(events)
            self._events[session_id] = events[:target_index]
            self._counters[session_id] = target_index
            self._save()
            return original_len - target_index

    def get_event_count(self, session_id: str) -> int:
        with self._lock:
            return self._counters.get(session_id, 0)


# ── PostgreSQL repositories ──────────────────────────────────────────────


class PostgresSessionRepository:
    """PostgreSQL-backed session metadata repository."""

    def __init__(self, pool):
        self._pool = pool

    async def _ensure_table(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    account_id   TEXT DEFAULT '',
                    status       TEXT DEFAULT 'active',
                    creator_id   TEXT DEFAULT '',
                    channel_type TEXT DEFAULT 'console',
                    tags         JSONB DEFAULT '[]',
                    created_at   TIMESTAMPTZ DEFAULT NOW(),
                    updated_at   TIMESTAMPTZ DEFAULT NOW(),
                    metadata     JSONB DEFAULT '{}'
                )
            """)

    async def save(self, session: Session) -> None:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (session_id, account_id, status, creator_id,
                    channel_type, tags, created_at, updated_at, metadata)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
                ON CONFLICT (session_id) DO UPDATE SET
                    account_id=EXCLUDED.account_id, status=EXCLUDED.status,
                    creator_id=EXCLUDED.creator_id, channel_type=EXCLUDED.channel_type,
                    tags=EXCLUDED.tags, updated_at=EXCLUDED.updated_at,
                    metadata=EXCLUDED.metadata
                """,
                session.session_id, session.account_id, session.status.value,
                session.creator_id, session.channel_type,
                json.dumps(session.tags),
                session.created_at, session.updated_at,
                json.dumps(session.metadata),
            )

    async def get(self, session_id: str) -> Optional[Session]:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE session_id=$1", session_id
            )
            if not row:
                return None
            return self._row_to_session(row)

    async def find_active_by_account(self, account_id: str) -> Optional[Session]:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE account_id=$1 AND status='active' "
                "ORDER BY updated_at DESC LIMIT 1",
                account_id,
            )
            if not row:
                return None
            return self._row_to_session(row)

    async def list_all(self) -> List[Session]:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM sessions ORDER BY created_at DESC")
            return [self._row_to_session(r) for r in rows]

    @staticmethod
    def _row_to_session(row) -> Session:
        from .models import SessionStatus
        status = row["status"]
        if isinstance(status, str):
            status = SessionStatus(status)
        return Session(
            session_id=row["session_id"],
            account_id=row.get("account_id", ""),
            status=status,
            creator_id=row.get("creator_id", ""),
            channel_type=row.get("channel_type", "console"),
            tags=json.loads(row["tags"]) if isinstance(row["tags"], str) else (row["tags"] or []),
            created_at=row["created_at"].timestamp() if hasattr(row["created_at"], "timestamp") else row["created_at"],
            updated_at=row["updated_at"].timestamp() if hasattr(row["updated_at"], "timestamp") else row["updated_at"],
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
        )


class PostgresEventRepository:
    """PostgreSQL-backed event log repository."""

    def __init__(self, pool):
        self._pool = pool

    async def _ensure_table(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id     TEXT PRIMARY KEY,
                    session_id   TEXT NOT NULL,
                    event_index  INTEGER NOT NULL,
                    event_type   TEXT NOT NULL,
                    content      JSONB DEFAULT '{}',
                    metadata     JSONB DEFAULT '{}',
                    timestamp    TIMESTAMPTZ DEFAULT NOW(),
                    UNIQUE (session_id, event_index)
                )
            """)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_events_session "
                "ON events(session_id, event_index)"
            )

    async def append_event(self, session_id: str, event: EventRecord) -> int:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            max_idx = await conn.fetchval(
                "SELECT COALESCE(MAX(event_index), -1) FROM events WHERE session_id=$1",
                session_id,
            )
            event.event_index = max_idx + 1
            await conn.execute(
                """
                INSERT INTO events (event_id, session_id, event_index, event_type,
                    content, metadata, timestamp)
                VALUES ($1,$2,$3,$4,$5,$6,$7)
                ON CONFLICT (event_id) DO NOTHING
                """,
                event.event_id, session_id, event.event_index, event.event_type,
                json.dumps(event.content), json.dumps(event.metadata),
                event.timestamp,
            )
            return event.event_index

    async def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[EventRecord]:
        await self._ensure_table()
        query = "SELECT * FROM events WHERE session_id=$1"
        params: list = [session_id]
        idx = 2
        if event_types:
            placeholders = ", ".join(f"${idx + i}" for i in range(len(event_types)))
            query += f" AND event_type IN ({placeholders})"
            params.extend(event_types)
            idx += len(event_types)
        query += " ORDER BY event_index ASC"
        if limit is not None:
            query += f" OFFSET ${idx} LIMIT ${idx + 1}"
            params.extend([offset, limit])
        else:
            query += f" OFFSET ${idx}"
            params.append(offset)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [self._row_to_event(r) for r in rows]

    async def truncate_events(self, session_id: str, target_index: int) -> int:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM events WHERE session_id=$1 AND event_index >= $2",
                session_id, target_index,
            )
            deleted = int(result.split()[-1]) if result else 0
            return deleted

    async def get_event_count(self, session_id: str) -> int:
        await self._ensure_table()
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM events WHERE session_id=$1", session_id
            ) or 0

    @staticmethod
    def _row_to_event(row) -> EventRecord:
        return EventRecord(
            event_id=row["event_id"],
            session_id=row["session_id"],
            event_index=row["event_index"],
            timestamp=row["timestamp"].timestamp() if hasattr(row["timestamp"], "timestamp") else row["timestamp"],
            event_type=row["event_type"],
            content=json.loads(row["content"]) if isinstance(row["content"], str) else (row["content"] or {}),
            metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else (row["metadata"] or {}),
        )


class PostgresAccountRepository:
    """PostgreSQL-backed account repository."""

    def __init__(self, pool):
        self._pool = pool

    async def _ensure_tables(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    account_id  TEXT PRIMARY KEY,
                    created_at  TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS account_bindings (
                    account_id       TEXT REFERENCES accounts(account_id) ON DELETE CASCADE,
                    channel_type     TEXT NOT NULL,
                    channel_user_id  TEXT NOT NULL,
                    created_at       TIMESTAMPTZ DEFAULT NOW(),
                    PRIMARY KEY (channel_type, channel_user_id)
                )
            """)

    async def find_by_binding(
        self, channel_type: str, channel_user_id: str
    ) -> Optional[str]:
        await self._ensure_tables()
        async with self._pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT account_id FROM account_bindings "
                "WHERE channel_type=$1 AND channel_user_id=$2",
                channel_type, channel_user_id,
            )

    async def create_account(
        self, channel_type: str, channel_user_id: str, account_id: str = ""
    ) -> str:
        import uuid
        await self._ensure_tables()
        if not account_id:
            account_id = str(uuid.uuid4())
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO accounts (account_id) VALUES ($1) ON CONFLICT DO NOTHING",
                account_id,
            )
            await conn.execute(
                "INSERT INTO account_bindings (account_id, channel_type, channel_user_id) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                account_id, channel_type, channel_user_id,
            )
        return account_id

    async def add_binding(
        self, account_id: str, channel_type: str, channel_user_id: str
    ) -> None:
        await self._ensure_tables()
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO account_bindings (account_id, channel_type, channel_user_id) "
                "VALUES ($1,$2,$3) ON CONFLICT DO NOTHING",
                account_id, channel_type, channel_user_id,
            )