"""
WorkspaceManager —— 多用户工作目录生命周期管理。

============================================================================
核心职责
============================================================================

  1. 用户目录初始化: 首次使用时创建 users_workspace/<user_uuid>/ 骨架
  2. 会话创建/结束/清理: session 目录创建、状态更新、过期清理
  3. 路径生成: 统一管理所有路径，避免散落各处的字符串拼接
  4. nsjail 集成: 生成 bind mount 参数列表
  5. YAML 文件读写: session.yaml / plan.yaml / user_profile.yaml

============================================================================
线程安全
============================================================================

  目录操作使用 os.makedirs(exist_ok=True) 幂等创建。
  SQLite 操作通过 WorkspaceDB 内部的连接工厂保证线程安全。
  YAML 写入使用原子写（先写 .tmp 再 os.replace）。

============================================================================
使用示例
============================================================================

  wm = WorkspaceManager(root=Path("users_workspace"))
  session = wm.create_session(user_uuid="u1", session_id="s1")
  bind_mounts = wm.get_nsjail_mounts(user_uuid="u1", session_id="s1")
  wm.end_session("s1", status=SessionStatus.COMPLETED)
  wm.cleanup_expired_sessions(max_age_days=30)
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from .models import User, Session, SessionStatus, Artifact
from .db import WorkspaceDB

logger = logging.getLogger("HpAgent.Workspace")


class WorkspaceManager:
    """多用户工作目录管理器。

    Attributes:
        root: 工作区根目录（如 Path("users_workspace")）。
        db: SQLite 数据库实例。
        _skill_dirs: 已注册的技能目录映射 (user_uuid → skill_dir)。
    """

    # ── 子目录名常量 ──
    DIR_SKILLS = "skills"
    DIR_SESSIONS = "sessions"
    DIR_CONVERSATION = "conversation"
    DIR_EXECUTION = "execution"
    DIR_LOGS = "logs"
    DIR_WORKSPACE = "workspace"
    DIR_INPUT = "input"
    DIR_SCRATCH = "scratch"
    DIR_OUTPUT = "output"
    DIR_RESOURCES = "resources"
    DIR_PERSISTENT = "persistent"

    # ── 文件名常量 ──
    FILE_SESSION_YAML = "session.yaml"
    FILE_PLAN_YAML = "plan.yaml"
    FILE_MESSAGES_JSONL = "messages.jsonl"
    FILE_SUMMARY_MD = "summary.md"
    FILE_RESOURCE_MANIFEST = "resource_manifest.yaml"
    FILE_USER_PROFILE = "user_profile.yaml"

    def __init__(self, root: Path, db_path: Optional[str] = None):
        """初始化工作区管理器。

        Args:
            root: 工作区根目录的绝对路径。
            db_path: SQLite 数据库文件路径。None 则默认放在 root / "workspace.db"。
        """
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = WorkspaceDB(db_path or str(self.root / "workspace.db"))

    # ══════════════════════════════════════════════════════════════════════
    # 用户管理
    # ══════════════════════════════════════════════════════════════════════

    def ensure_user(self, user_uuid: str, username: str = "") -> User:
        """确保用户工作目录存在（幂等）。

        首次调用时创建完整的用户目录骨架和 DB 记录。
        后续调用仅返回已有的 User 对象。

        Args:
            user_uuid: 用户 UUID 字符串。
            username: 可读用户名（可选）。

        Returns:
            User 实例。
        """
        user_dir = self._user_dir(user_uuid)
        profile_path = str(user_dir / self.FILE_USER_PROFILE)
        persistent_dir = str(user_dir / self.DIR_PERSISTENT)

        # 幂等创建目录骨架
        for subdir in [
            user_dir / self.DIR_SKILLS,
            user_dir / self.DIR_SESSIONS,
            user_dir / self.DIR_PERSISTENT,
        ]:
            subdir.mkdir(parents=True, exist_ok=True)

        # 初始化 user_profile.yaml（如果不存在）
        profile_file = user_dir / self.FILE_USER_PROFILE
        if not profile_file.exists():
            self._write_yaml(profile_file, {
                "user_uuid": user_uuid,
                "username": username,
                "preferences": {},
                "created_at": self._now_iso(),
            })

        user = User(
            uuid=user_uuid,
            username=username,
            profile_path=profile_path,
            persistent_dir=persistent_dir,
            created_at=self._now_iso(),
        )
        self.db.upsert_user(user)
        return user

    # ══════════════════════════════════════════════════════════════════════
    # 会话生命周期
    # ══════════════════════════════════════════════════════════════════════

    def create_session(
        self,
        user_uuid: str,
        session_id: Optional[str] = None,
        *,
        task_summary: str = "",
        tags: Optional[list[str]] = None,
    ) -> Session:
        """创建新会话并初始化完整目录结构。

        目录结构:
          sessions/<session_id>/
            ├── session.yaml
            ├── conversation/
            │   ├── messages.jsonl  (空文件)
            │   └── summary.md      (空文件)
            ├── execution/
            │   ├── plan.yaml
            │   └── logs/
            ├── workspace/
            │   ├── input/
            │   ├── scratch/
            │   └── output/
            └── resources/
                └── resource_manifest.yaml

        Args:
            user_uuid: 所属用户 UUID。
            session_id: 会话 ID，None 自动生成（含时间戳以避免碰撞）。
            task_summary: 初始任务描述。
            tags: 标签列表。

        Returns:
            新创建的 Session 实例。
        """
        if session_id is None:
            session_id = f"sess_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

        self.ensure_user(user_uuid)
        session_dir = self._session_dir(user_uuid, session_id)

        # 创建所有子目录
        subdirs = [
            session_dir / self.DIR_CONVERSATION,
            session_dir / self.DIR_EXECUTION / self.DIR_LOGS,
            session_dir / self.DIR_WORKSPACE / self.DIR_INPUT,
            session_dir / self.DIR_WORKSPACE / self.DIR_SCRATCH,
            session_dir / self.DIR_WORKSPACE / self.DIR_OUTPUT,
            session_dir / self.DIR_RESOURCES,
        ]
        for d in subdirs:
            d.mkdir(parents=True, exist_ok=True)

        # 创建初始文件
        session_yaml = session_dir / self.FILE_SESSION_YAML
        plan_file = session_dir / self.DIR_EXECUTION / self.FILE_PLAN_YAML
        messages_file = session_dir / self.DIR_CONVERSATION / self.FILE_MESSAGES_JSONL
        summary_file = session_dir / self.DIR_CONVERSATION / self.FILE_SUMMARY_MD
        manifest_file = session_dir / self.DIR_RESOURCES / self.FILE_RESOURCE_MANIFEST

        now = self._now_iso()
        session_data = {
            "session_id": session_id,
            "user_uuid": user_uuid,
            "status": SessionStatus.ACTIVE.value,
            "task_summary": task_summary,
            "tags": tags or [],
            "created_at": now,
        }
        self._write_yaml(session_yaml, session_data)
        self._write_yaml(plan_file, {"steps": [], "created_at": now})
        self._write_yaml(manifest_file, {"resources": [], "created_at": now})

        # 创建空文件
        messages_file.touch()
        summary_file.touch()

        # 构建相对路径（相对于 user 根目录）
        user_root = self._user_dir(user_uuid)
        session = Session(
            session_id=session_id,
            user_uuid=user_uuid,
            status=SessionStatus.ACTIVE,
            task_summary=task_summary,
            session_dir=str(session_dir.relative_to(user_root)),
            plan_file=str(plan_file.relative_to(user_root)),
            conversation_file=str(messages_file.relative_to(user_root)),
            output_dir=str((session_dir / self.DIR_WORKSPACE / self.DIR_OUTPUT).relative_to(user_root)),
            tags=tags or [],
            created_at=now,
            updated_at=now,
        )
        self.db.insert_session(session)
        logger.info("Session created: %s for user %s", session_id, user_uuid)
        return session

    def end_session(self, session_id: str, status: SessionStatus, task_summary: str = "") -> None:
        """结束会话并更新状态。

        Args:
            session_id: 会话 ID。
            status: 结束状态（COMPLETED 或 FAILED）。
            task_summary: 最终的会话摘要。
        """
        session = self.db.get_session(session_id)
        if session is None:
            logger.warning("end_session called for unknown session: %s", session_id)
            return

        # 更新 session.yaml
        user_root = self._user_dir(session.user_uuid)
        yaml_path = user_root / session.session_dir / self.FILE_SESSION_YAML
        if yaml_path.exists():
            data = self._read_yaml(yaml_path) or {}
            data["status"] = status.value
            if task_summary:
                data["task_summary"] = task_summary
            data["ended_at"] = self._now_iso()
            self._write_yaml(yaml_path, data)

        self.db.update_session_status(session_id, status, task_summary)
        logger.info("Session %s ended: %s", session_id, status.value)

    def get_session(self, session_id: str) -> Optional[Session]:
        """按 ID 查询会话。"""
        return self.db.get_session(session_id)

    def list_sessions(self, user_uuid: str, limit: int = 50, offset: int = 0) -> list[Session]:
        """列出用户的所有会话。"""
        return self.db.list_sessions(user_uuid, limit, offset)

    # ══════════════════════════════════════════════════════════════════════
    # 会话清理
    # ══════════════════════════════════════════════════════════════════════

    def cleanup_expired_sessions(self, max_age_days: int = 30, dry_run: bool = False) -> int:
        """清理超过 max_age_days 天未更新的已完成/失败会话。

        清理流程:
          1. 从 DB 查询所有 completed/failed 状态的会话
          2. 检查 updated_at 是否超过阈值
          3. 删除 session 目录（保留 persistent/ 中已同步的产出）
          4. 更新 DB 状态为 deleted

        Args:
            max_age_days: 过期天数阈值。
            dry_run: True 时只返回待清理数量，不实际删除。

        Returns:
            已清理（或待清理）的会话数量。
        """
        import sqlite3
        conn = self.db._connect()
        try:
            cutoff = self._now_iso()
            rows = conn.execute(
                """SELECT session_id, user_uuid, session_dir, output_dir FROM sessions
                   WHERE status IN ('completed', 'failed')
                   AND updated_at < datetime(?, ?)""",
                (cutoff, f"-{max_age_days} days"),
            ).fetchall()
        finally:
            conn.close()

        cleaned = 0
        for row in rows:
            if not dry_run:
                session_dir = self._user_dir(row["user_uuid"]) / row["session_dir"]
                if session_dir.exists():
                    shutil.rmtree(session_dir)
                self.db.update_session_status(
                    row["session_id"], SessionStatus.DELETED
                )
            cleaned += 1

        if cleaned:
            logger.info("Cleaned %d expired sessions (dry_run=%s)", cleaned, dry_run)
        return cleaned

    # ══════════════════════════════════════════════════════════════════════
    # nsjail 集成
    # ══════════════════════════════════════════════════════════════════════

    def get_nsjail_mounts(self, user_uuid: str, session_id: str) -> list[str]:
        """生成 nsjail --bindmount / --bindmount_ro 参数列表。

        挂载策略:
          - workspace/ 子目录 → 读写挂载（Agent 需要在其中创建/修改文件）
          - skills/ 子目录 → 只读挂载（Agent 只能读取技能定义）
          - 目标路径统一映射到 /work 下

        Args:
            user_uuid: 用户 UUID。
            session_id: 会话 ID。

        Returns:
            ["src:dst:rw", "src:dst:ro", ...] 格式的 nsjail bind mount 参数。
        """
        user_dir = self._user_dir(user_uuid)
        session_dir = self._session_dir(user_uuid, session_id)
        mounts = []

        # 读写挂载: workspace/
        ws_dir = session_dir / self.DIR_WORKSPACE
        if ws_dir.exists():
            mounts.append(f"{ws_dir}:/work")

        # 只读挂载: skills/
        skills_dir = user_dir / self.DIR_SKILLS
        if skills_dir.exists() and any(skills_dir.iterdir()):
            mounts.append(f"{skills_dir}:/skills")

        return mounts

    def get_session_work_dir(self, user_uuid: str, session_id: str) -> Path:
        """返回会话工作区的宿主绝对路径。

        供 nsjail --chroot 或直接文件访问使用。
        """
        return self._session_dir(user_uuid, session_id) / self.DIR_WORKSPACE

    def get_session_output_dir(self, user_uuid: str, session_id: str) -> Path:
        """返回会话产出目录的宿主绝对路径。"""
        return self._session_dir(user_uuid, session_id) / self.DIR_WORKSPACE / self.DIR_OUTPUT

    # ══════════════════════════════════════════════════════════════════════
    # 产出物管理
    # ══════════════════════════════════════════════════════════════════════

    def register_artifact(
        self,
        session_id: str,
        file_path: str,
        file_type: str = "",
    ) -> Optional[Artifact]:
        """注册一个会话产出物。

        Args:
            session_id: 所属会话 ID。
            file_path: workspace/ 下的相对文件路径。
            file_type: MIME 类型或扩展名。

        Returns:
            Artifact 实例，或 None（当 session 不存在时）。
        """
        session = self.db.get_session(session_id)
        if session is None:
            return None

        # 计算文件实际信息
        full_path = self._user_dir(session.user_uuid) / session.session_dir / self.DIR_WORKSPACE / file_path
        file_size = 0
        if full_path.exists():
            file_size = full_path.stat().st_size

        artifact = Artifact(
            artifact_id=str(uuid.uuid4()),
            session_id=session_id,
            file_path=file_path,
            file_type=file_type,
            file_size=file_size,
            created_at=self._now_iso(),
        )
        self.db.insert_artifact(artifact)
        return artifact

    def list_artifacts(self, session_id: str) -> list[Artifact]:
        """列出会话的所有产出物。"""
        return self.db.list_artifacts(session_id)

    # ══════════════════════════════════════════════════════════════════════
    # YAML 读写
    # ══════════════════════════════════════════════════════════════════════

    def _read_yaml(self, path: Path) -> Optional[dict]:
        """读取 YAML 文件，文件不存在时返回 None。"""
        if not path.exists():
            return None
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def _write_yaml(self, path: Path, data: dict) -> None:
        """原子写入 YAML 文件（先写 .tmp 再 rename）。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        try:
            with open(tmp, "w") as f:
                yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)
            os.replace(tmp, path)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ══════════════════════════════════════════════════════════════════════
    # 路径工具
    # ══════════════════════════════════════════════════════════════════════

    def _user_dir(self, user_uuid: str) -> Path:
        return self.root / user_uuid

    def _session_dir(self, user_uuid: str, session_id: str) -> Path:
        return self._user_dir(user_uuid) / self.DIR_SESSIONS / session_id

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
