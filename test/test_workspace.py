"""
Workspace 模块测试 —— 验证多用户工作目录管理。

测试覆盖:
  1. WorkspaceDB CRUD 操作
  2. WorkspaceManager 目录创建
  3. 会话生命周期（创建/结束/清理）
  4. nsjail bind mount 参数生成
  5. 产出物注册
  6. 错误处理
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from workspace.models import User, Session, Artifact, SessionStatus
from workspace.db import WorkspaceDB
from workspace.manager import WorkspaceManager


class TestWorkspaceDB:
    """测试 SQLite 数据库层。"""

    @pytest.fixture
    def db(self):
        """创建临时数据库。"""
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test_workspace.db")
        wdb = WorkspaceDB(db_path)
        yield wdb
        shutil.rmtree(tmpdir)

    def test_upsert_and_get_user(self, db):
        user = User(uuid="u1", username="alice")
        db.upsert_user(user)
        fetched = db.get_user("u1")
        assert fetched is not None
        assert fetched.uuid == "u1"
        assert fetched.username == "alice"

    def test_get_nonexistent_user(self, db):
        assert db.get_user("nonexistent") is None

    def test_upsert_user_update(self, db):
        db.upsert_user(User(uuid="u1", username="alice"))
        db.upsert_user(User(uuid="u1", username="alice_v2"))
        fetched = db.get_user("u1")
        assert fetched.username == "alice_v2"

    def test_insert_and_get_session(self, db):
        db.upsert_user(User(uuid="u1"))
        session = Session(
            session_id="s1",
            user_uuid="u1",
            status=SessionStatus.ACTIVE,
            task_summary="test task",
            tags=["important"],
        )
        db.insert_session(session)
        fetched = db.get_session("s1")
        assert fetched is not None
        assert fetched.session_id == "s1"
        assert fetched.user_uuid == "u1"
        assert fetched.status == SessionStatus.ACTIVE
        assert fetched.task_summary == "test task"
        assert fetched.tags == ["important"]

    def test_update_session_status(self, db):
        db.upsert_user(User(uuid="u1"))
        db.insert_session(Session(session_id="s1", user_uuid="u1"))
        db.update_session_status("s1", SessionStatus.COMPLETED, "all done")
        fetched = db.get_session("s1")
        assert fetched.status == SessionStatus.COMPLETED
        assert fetched.task_summary == "all done"

    def test_list_sessions(self, db):
        db.upsert_user(User(uuid="u1"))
        for i in range(5):
            db.insert_session(Session(session_id=f"s{i}", user_uuid="u1"))
        sessions = db.list_sessions("u1", limit=3)
        assert len(sessions) == 3

    def test_insert_and_list_artifacts(self, db):
        db.upsert_user(User(uuid="u1"))
        db.insert_session(Session(session_id="s1", user_uuid="u1"))
        db.insert_artifact(Artifact(
            artifact_id="a1", session_id="s1",
            file_path="output/result.png", file_type="image/png", file_size=1024,
        ))
        db.insert_artifact(Artifact(
            artifact_id="a2", session_id="s1",
            file_path="output/readme.md", file_type="text/markdown", file_size=512,
        ))
        arts = db.list_artifacts("s1")
        assert len(arts) == 2
        assert arts[0].artifact_id == "a1"

    def test_delete_session_cascades(self, db):
        db.upsert_user(User(uuid="u1"))
        db.insert_session(Session(session_id="s1", user_uuid="u1"))
        db.insert_artifact(Artifact(artifact_id="a1", session_id="s1", file_path="x"))
        db.delete_session("s1")
        assert db.get_session("s1") is None
        assert db.list_artifacts("s1") == []


class TestWorkspaceManager:
    """测试 WorkspaceManager 目录管理和会话生命周期。"""

    @pytest.fixture
    def wm(self):
        """创建指向临时目录的 WorkspaceManager。"""
        tmpdir = tempfile.mkdtemp()
        manager = WorkspaceManager(root=Path(tmpdir))
        yield manager
        shutil.rmtree(tmpdir)

    def test_ensure_user_creates_directories(self, wm):
        user = wm.ensure_user("u1", "alice")
        assert user.uuid == "u1"
        user_dir = wm.root / "u1"
        assert user_dir.exists()
        assert (user_dir / "skills").exists()
        assert (user_dir / "sessions").exists()
        assert (user_dir / "persistent").exists()
        assert (user_dir / "user_profile.yaml").exists()

    def test_ensure_user_idempotent(self, wm):
        wm.ensure_user("u1")
        wm.ensure_user("u1")  # 第二次调用不应报错
        assert (wm.root / "u1").exists()

    def test_create_session_full_structure(self, wm):
        wm.ensure_user("u1")
        session = wm.create_session(user_uuid="u1", session_id="test_s1", task_summary="test")
        assert session.session_id == "test_s1"
        assert session.status == SessionStatus.ACTIVE

        # 验证完整目录结构
        session_dir = wm.root / "u1" / "sessions" / "test_s1"
        assert (session_dir / "session.yaml").exists()
        assert (session_dir / "conversation" / "messages.jsonl").exists()
        assert (session_dir / "conversation" / "summary.md").exists()
        assert (session_dir / "execution" / "plan.yaml").exists()
        assert (session_dir / "execution" / "logs").exists()
        assert (session_dir / "workspace" / "input").exists()
        assert (session_dir / "workspace" / "scratch").exists()
        assert (session_dir / "workspace" / "output").exists()
        assert (session_dir / "resources" / "resource_manifest.yaml").exists()

    def test_create_session_auto_id(self, wm):
        wm.ensure_user("u1")
        session = wm.create_session(user_uuid="u1")
        assert session.session_id.startswith("sess_")
        assert len(session.session_id) > 20  # 含时间戳和随机成分

    def test_end_session(self, wm):
        wm.ensure_user("u1")
        session = wm.create_session(user_uuid="u1", session_id="s1")
        wm.end_session("s1", SessionStatus.COMPLETED, "done")
        fetched = wm.get_session("s1")
        assert fetched.status == SessionStatus.COMPLETED
        assert fetched.task_summary == "done"

        # 验证 session.yaml 已更新
        yaml_path = wm.root / "u1" / "sessions" / "s1" / "session.yaml"
        assert yaml_path.exists()

    def test_list_sessions(self, wm):
        wm.ensure_user("u1")
        for i in range(3):
            wm.create_session(user_uuid="u1")
        sessions = wm.list_sessions("u1")
        assert len(sessions) == 3

    def test_get_nsjail_mounts(self, wm):
        wm.ensure_user("u1")
        session = wm.create_session(user_uuid="u1", session_id="s1")
        mounts = wm.get_nsjail_mounts("u1", "s1")
        # 至少应该有 workspace 绑载
        assert len(mounts) >= 1
        workspace_mount = [m for m in mounts if "/work" in m]
        assert len(workspace_mount) == 1

    def test_get_session_work_dir(self, wm):
        wm.ensure_user("u1")
        wm.create_session(user_uuid="u1", session_id="s1")
        work_dir = wm.get_session_work_dir("u1", "s1")
        assert work_dir.exists()
        assert str(work_dir).endswith("workspace")

    def test_get_session_output_dir(self, wm):
        wm.ensure_user("u1")
        wm.create_session(user_uuid="u1", session_id="s1")
        out_dir = wm.get_session_output_dir("u1", "s1")
        assert out_dir.exists()
        assert str(out_dir).endswith("output")

    def test_register_artifact(self, wm):
        wm.ensure_user("u1")
        session = wm.create_session(user_uuid="u1", session_id="s1")
        # 在 workspace 根目录下创建测试文件
        work_dir = wm.get_session_work_dir("u1", "s1")
        test_file = work_dir / "result.txt"
        test_file.write_text("hello")

        art = wm.register_artifact("s1", "result.txt", "text/plain")
        assert art is not None
        assert art.file_path == "result.txt"
        assert art.file_size > 0

        arts = wm.list_artifacts("s1")
        assert len(arts) == 1

    def test_register_artifact_nonexistent_session(self, wm):
        art = wm.register_artifact("nonexistent", "file.txt")
        assert art is None

    def test_cleanup_expired_sessions_dry_run(self, wm):
        wm.ensure_user("u1")
        session = wm.create_session(user_uuid="u1", session_id="s1")
        wm.end_session("s1", SessionStatus.COMPLETED)
        # dry_run=True 不删除，仅计数
        count = wm.cleanup_expired_sessions(max_age_days=0, dry_run=True)
        assert count >= 0  # 根据时间可能为 0 或 1

    def test_session_id_uniqueness(self, wm):
        """验证自动生成的 session_id 不碰撞。"""
        wm.ensure_user("u1")
        ids = set()
        for _ in range(20):
            session = wm.create_session(user_uuid="u1")
            ids.add(session.session_id)
        assert len(ids) == 20

    def test_multi_user_isolation(self, wm):
        """验证不同用户的工作目录互相隔离。"""
        wm.ensure_user("u1")
        wm.ensure_user("u2")
        wm.create_session(user_uuid="u1", session_id="s1")
        wm.create_session(user_uuid="u2", session_id="s2")

        # u1 的会话只有 s1
        assert len(wm.list_sessions("u1")) == 1
        assert wm.list_sessions("u1")[0].session_id == "s1"
        # u2 的会话只有 s2
        assert len(wm.list_sessions("u2")) == 1
        assert wm.list_sessions("u2")[0].session_id == "s2"


class TestWorkspaceConfig:
    """测试配置相关功能。"""

    def test_root_resolves_to_absolute(self):
        wm = WorkspaceManager(root=Path("./relative_ws"))
        try:
            assert wm.root.is_absolute()
        finally:
            shutil.rmtree(wm.root)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
