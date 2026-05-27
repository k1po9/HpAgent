"""
Session Workspace —— 会话工作目录初始化。

所有 I/O 通过 LocalFileStore。
路径约定:
  <user_uuid>/
    ├── skills/
    ├── sessions/<session_id>/
    │   ├── session.yaml
    │   └── workspace/  (input/ scratch/ output/)
    ├── persistent/
    └── user_profile.yaml
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from .db import WorkspaceDB
from .models import Session, SessionStatus

logger = logging.getLogger("HpAgent.SessionWorkspace")

DIR_SKILLS = "skills"
DIR_SESSIONS = "sessions"
DIR_WORKSPACE = "workspace"
DIR_INPUT = "input"
DIR_SCRATCH = "scratch"
DIR_OUTPUT = "output"
DIR_PERSISTENT = "persistent"
FILE_SESSION_YAML = "session.yaml"
FILE_USER_PROFILE = "user_profile.yaml"


def init_user(file_store, db: WorkspaceDB, user_uuid: str, username: str = "") -> None:
    """确保用户工作目录存在（幂等）。"""

    for subdir in [DIR_SKILLS, DIR_SESSIONS, DIR_PERSISTENT]:
        file_store.mkdir_sync(f"{user_uuid}/{subdir}")

    profile_rel = f"{user_uuid}/{FILE_USER_PROFILE}"
    if not file_store.exists_sync(profile_rel):
        _write_yaml(file_store, profile_rel, {
            "user_uuid": user_uuid,
            "username": username,
            "preferences": {},
            "created_at": _now_iso(),
        })

    db.upsert_user(
        user_uuid=user_uuid,
        username=username,
        profile_path=profile_rel,
        persistent_dir=f"{user_uuid}/{DIR_PERSISTENT}",
    )


def init_session(
    file_store,
    db: WorkspaceDB,
    user_uuid: str,
    session_id: Optional[str] = None,
    *,
    task_summary: str = "",
    tags: Optional[list[str]] = None,
) -> Session:
    """创建新会话并初始化工作目录。

    创建:
      sessions/<session_id>/
        ├── session.yaml
        └── workspace/  (input/ scratch/ output/)
    """
    if session_id is None:
        session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    session_rel = f"{user_uuid}/{DIR_SESSIONS}/{session_id}"

    for sub in [
        f"{session_rel}/{DIR_WORKSPACE}/{DIR_INPUT}",
        f"{session_rel}/{DIR_WORKSPACE}/{DIR_SCRATCH}",
        f"{session_rel}/{DIR_WORKSPACE}/{DIR_OUTPUT}",
    ]:
        file_store.mkdir_sync(sub)

    now = _now_iso()
    _write_yaml(file_store, f"{session_rel}/{FILE_SESSION_YAML}", {
        "session_id": session_id,
        "user_uuid": user_uuid,
        "status": SessionStatus.ACTIVE.value,
        "task_summary": task_summary,
        "tags": tags or [],
        "created_at": now,
    })

    session = Session(
        session_id=session_id,
        account_id=user_uuid,
        status=SessionStatus.ACTIVE,
        task_summary=task_summary,
        session_dir=session_rel,
        output_dir=f"{session_rel}/{DIR_WORKSPACE}/{DIR_OUTPUT}",
        tags=tags or [],
        created_at=time.time(),
        updated_at=time.time(),
    )
    db.insert_session(session)
    logger.info("Session initialized: %s for user %s", session_id, user_uuid)
    return session


def _write_yaml(file_store, rel_path: str, data: dict) -> None:
    import yaml
    content = yaml.safe_dump(data, default_flow_style=False, allow_unicode=True)
    file_store.write_atomic_sync(rel_path, content)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
