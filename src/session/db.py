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
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
