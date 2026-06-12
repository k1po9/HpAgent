"""
Session Database —— SQLite 元数据存储（仅用户 + 会话两张表）。
"""
from __future__ import annotations

import json
import sqlite3

from .models import Session


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    uuid TEXT PRIMARY KEY,
    username TEXT NOT NULL DEFAULT '',
    profile_path TEXT NOT NULL DEFAULT '',
    persistent_dir TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    user_uuid TEXT NOT NULL REFERENCES users(uuid) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active',
    task_summary TEXT NOT NULL DEFAULT '',
    session_dir TEXT NOT NULL DEFAULT '',
    plan_file TEXT NOT NULL DEFAULT '',
    conversation_file TEXT NOT NULL DEFAULT '',
    output_dir TEXT NOT NULL DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created
    ON sessions(user_uuid, created_at DESC);
"""


class WorkspaceDB:
    """SQLite 封装 —— 工作区元数据存储。"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        from pathlib import Path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(SCHEMA_SQL)
        finally:
            conn.close()

    def upsert_user(self, user_uuid: str, username: str = "",
                    profile_path: str = "", persistent_dir: str = "") -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO users (uuid, username, profile_path, persistent_dir)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(uuid) DO UPDATE SET
                       username=excluded.username,
                       profile_path=excluded.profile_path,
                       persistent_dir=excluded.persistent_dir""",
                (user_uuid, username, profile_path, persistent_dir),
            )
            conn.commit()
        finally:
            conn.close()

    def insert_session(self, session: Session) -> None:
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO sessions
                   (session_id, user_uuid, status, task_summary, session_dir,
                    plan_file, conversation_file, output_dir, tags)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(session_id) DO UPDATE SET
                       status=excluded.status,
                       task_summary=excluded.task_summary,
                       session_dir=excluded.session_dir,
                       updated_at=datetime('now')""",
                (
                    session.session_id,
                    session.account_id,
                    session.status.value,
                    session.task_summary,
                    session.session_dir,
                    session.plan_file,
                    session.conversation_file,
                    session.output_dir,
                    json.dumps(session.tags),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def get_session(self, session_id: str) -> Optional["Session"]:
        from .models import Session, SessionStatus
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT session_id, user_uuid, status, task_summary, session_dir,"
                "  plan_file, conversation_file, output_dir, tags,"
                "  CAST(strftime('%s', created_at) AS REAL),"
                "  CAST(strftime('%s', updated_at) AS REAL)"
                " FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if row is None:
                return None
            return Session(
                session_id=row[0],
                account_id=row[1],
                status=SessionStatus(row[2]),
                task_summary=row[3],
                session_dir=row[4],
                plan_file=row[5],
                conversation_file=row[6],
                output_dir=row[7],
                tags=json.loads(row[8]) if row[8] else [],
                created_at=float(row[9]) if row[9] else 0.0,
                updated_at=float(row[10]) if row[10] else 0.0,
            )
        finally:
            conn.close()

    def complete_session(self, session_id: str) -> None:
        """标记会话为已完成。"""
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE sessions SET status='completed', updated_at=datetime('now')"
                " WHERE session_id = ?",
                (session_id,),
            )
            conn.commit()
        finally:
            conn.close()
