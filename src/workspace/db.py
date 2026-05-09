"""
Workspace 数据库层 —— SQLite 元数据存储。

============================================================================
设计
============================================================================

  使用标准库 sqlite3（同步，线程安全），WAL 模式提升并发。
  数据库文件路径由 WorkspaceManager 在初始化时指定。
  所有方法返回 models 层的数据类实例。

  表结构:
    - users:     用户注册信息
    - sessions:  会话元数据（状态、路径、标签）
    - artifacts: 产出物索引（不存文件内容，只存路径和类型）

============================================================================
线程安全
============================================================================

  sqlite3 在 check_same_thread=False + WAL 模式下支持多线程读。
  写入通过短超时重试解决偶发冲突（MVP 单 Worker 场景几乎不会触发）。
"""
from __future__ import annotations

import sqlite3
import time
from typing import Optional

from .models import User, Session, SessionStatus, Artifact


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
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_created
    ON sessions(user_uuid, created_at DESC);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    file_path TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT '',
    file_size INTEGER NOT NULL DEFAULT 0,
    checksum TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_artifacts_session
    ON artifacts(session_id);
"""


class WorkspaceDB:
    """SQLite 数据库封装 —— 工作区元数据的唯一读写入口。

    Attributes:
        db_path: SQLite 数据库文件的绝对路径。
        _conn_factory: 每次操作创建新连接（避免跨线程共享连接）。
    """

    def __init__(self, db_path: str):
        """初始化数据库连接并执行 DDL。

        Args:
            db_path: SQLite 数据库文件路径（如 "data/workspace.db"）。
        """
        self.db_path = db_path
        self._ensure_schema()

    # ── 内部 ──

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

    # ── 用户操作 ──

    def upsert_user(self, user: User) -> None:
        """插入或更新用户记录。"""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO users (uuid, username, profile_path, persistent_dir)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(uuid) DO UPDATE SET
                       username=excluded.username,
                       profile_path=excluded.profile_path,
                       persistent_dir=excluded.persistent_dir""",
                (user.uuid, user.username, user.profile_path, user.persistent_dir),
            )
            conn.commit()
        finally:
            conn.close()

    def get_user(self, user_uuid: str) -> Optional[User]:
        """按 UUID 查询用户。"""
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM users WHERE uuid=?", (user_uuid,)).fetchone()
            if row is None:
                return None
            return User(
                uuid=row["uuid"],
                username=row["username"],
                profile_path=row["profile_path"],
                persistent_dir=row["persistent_dir"],
                created_at=row["created_at"],
            )
        finally:
            conn.close()

    # ── 会话操作 ──

    def insert_session(self, session: Session) -> None:
        """插入新会话记录。"""
        import json
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO sessions
                   (session_id, user_uuid, status, task_summary, session_dir,
                    plan_file, conversation_file, output_dir, tags, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session.session_id,
                    session.user_uuid,
                    session.status.value,
                    session.task_summary,
                    session.session_dir,
                    session.plan_file,
                    session.conversation_file,
                    session.output_dir,
                    json.dumps(session.tags),
                    session.metadata_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def update_session_status(self, session_id: str, status: SessionStatus, task_summary: str = "") -> None:
        """更新会话状态和摘要。"""
        conn = self._connect()
        try:
            params = [status.value, "datetime('now')", session_id]
            if task_summary:
                conn.execute(
                    "UPDATE sessions SET status=?, task_summary=?, updated_at=datetime('now') WHERE session_id=?",
                    (status.value, task_summary, session_id),
                )
            else:
                conn.execute(
                    "UPDATE sessions SET status=?, updated_at=datetime('now') WHERE session_id=?",
                    (status.value, session_id),
                )
            conn.commit()
        finally:
            conn.close()

    def get_session(self, session_id: str) -> Optional[Session]:
        """按 session_id 查询会话。"""
        import json
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            if row is None:
                return None
            return Session(
                session_id=row["session_id"],
                user_uuid=row["user_uuid"],
                status=SessionStatus(row["status"]),
                task_summary=row["task_summary"],
                session_dir=row["session_dir"],
                plan_file=row["plan_file"],
                conversation_file=row["conversation_file"],
                output_dir=row["output_dir"],
                tags=json.loads(row["tags"]) if row["tags"] else [],
                metadata_json=row["metadata_json"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
        finally:
            conn.close()

    def list_sessions(self, user_uuid: str, limit: int = 50, offset: int = 0) -> list[Session]:
        """列出指定用户的会话，按创建时间倒序。"""
        import json
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE user_uuid=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (user_uuid, limit, offset),
            ).fetchall()
            return [
                Session(
                    session_id=r["session_id"],
                    user_uuid=r["user_uuid"],
                    status=SessionStatus(r["status"]),
                    task_summary=r["task_summary"],
                    session_dir=r["session_dir"],
                    plan_file=r["plan_file"],
                    conversation_file=r["conversation_file"],
                    output_dir=r["output_dir"],
                    tags=json.loads(r["tags"]) if r["tags"] else [],
                    metadata_json=r["metadata_json"],
                    created_at=r["created_at"],
                    updated_at=r["updated_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def delete_session(self, session_id: str) -> None:
        """删除会话记录（级联删除 artifacts）。"""
        conn = self._connect()
        try:
            conn.execute("DELETE FROM sessions WHERE session_id=?", (session_id,))
            conn.commit()
        finally:
            conn.close()

    # ── 产出物操作 ──

    def insert_artifact(self, artifact: Artifact) -> None:
        """插入一条产出物记录。"""
        conn = self._connect()
        try:
            conn.execute(
                """INSERT INTO artifacts (artifact_id, session_id, file_path, file_type, file_size, checksum)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (artifact.artifact_id, artifact.session_id, artifact.file_path,
                 artifact.file_type, artifact.file_size, artifact.checksum),
            )
            conn.commit()
        finally:
            conn.close()

    def list_artifacts(self, session_id: str) -> list[Artifact]:
        """列出指定会话的所有产出物。"""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE session_id=? ORDER BY created_at",
                (session_id,),
            ).fetchall()
            return [
                Artifact(
                    artifact_id=r["artifact_id"],
                    session_id=r["session_id"],
                    file_path=r["file_path"],
                    file_type=r["file_type"],
                    file_size=r["file_size"],
                    checksum=r["checksum"],
                    created_at=r["created_at"],
                )
                for r in rows
            ]
        finally:
            conn.close()

    def delete_artifacts(self, session_id: str) -> int:
        """删除指定会话的所有产出物记录，返回删除数量。"""
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM artifacts WHERE session_id=?", (session_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
