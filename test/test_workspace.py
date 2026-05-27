"""
Session Workspace 模块测试。
"""
import os
import sys
import tempfile
import shutil
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from session.models import Session, SessionStatus
from session.db import WorkspaceDB
from session.workspace import init_user, init_session
from storage.file_store import LocalFileStore


def _make_file_store(tmpdir: str) -> LocalFileStore:
    return LocalFileStore(root=Path(tmpdir))


class TestWorkspaceDB:
    """SQLite 数据库层测试。"""

    @pytest.fixture
    def db(self):
        tmpdir = tempfile.mkdtemp()
        db_path = os.path.join(tmpdir, "test_workspace.db")
        wdb = WorkspaceDB(db_path)
        yield wdb
        shutil.rmtree(tmpdir)

    def test_upsert_user(self, db):
        db.upsert_user("u1", "alice", "u1/user_profile.yaml", "u1/persistent")
        db.upsert_user("u1", "alice_v2", "u1/user_profile.yaml", "u1/persistent")

    def test_insert_session(self, db):
        db.upsert_user("u1")
        session = Session(
            session_id="s1",
            account_id="u1",
            status=SessionStatus.ACTIVE,
            task_summary="test task",
            tags=["important"],
            session_dir="u1/sessions/s1",
            output_dir="u1/sessions/s1/workspace/output",
        )
        db.insert_session(session)


class TestSessionWorkspace:
    """会话工作区目录初始化测试。"""

    @pytest.fixture
    def ctx(self):
        tmpdir = tempfile.mkdtemp()
        fs = _make_file_store(tmpdir)
        db_path = os.path.join(tmpdir, "test_workspace.db")
        db = WorkspaceDB(db_path)
        yield fs, db, Path(tmpdir)
        shutil.rmtree(tmpdir)

    def test_init_user_creates_directories(self, ctx):
        fs, db, root = ctx
        init_user(fs, db, "u1", "alice")
        assert (root / "u1" / "skills").exists()
        assert (root / "u1" / "sessions").exists()
        assert (root / "u1" / "persistent").exists()
        assert (root / "u1" / "user_profile.yaml").exists()

    def test_init_user_idempotent(self, ctx):
        fs, db, root = ctx
        init_user(fs, db, "u1")
        init_user(fs, db, "u1")

    def test_init_session_creates_workspace(self, ctx):
        fs, db, root = ctx
        init_user(fs, db, "u1")
        session = init_session(fs, db, user_uuid="u1", session_id="test_s1",
                               task_summary="test", tags=["urgent"])
        assert session.session_id == "test_s1"
        assert session.status == SessionStatus.ACTIVE
        assert session.task_summary == "test"
        assert session.tags == ["urgent"]
        assert session.account_id == "u1"

        session_dir = root / "u1" / "sessions" / "test_s1"
        assert (session_dir / "session.yaml").exists()
        assert (session_dir / "workspace" / "input").exists()
        assert (session_dir / "workspace" / "scratch").exists()
        assert (session_dir / "workspace" / "output").exists()

    def test_init_session_auto_id(self, ctx):
        fs, db, root = ctx
        init_user(fs, db, "u1")
        session = init_session(fs, db, user_uuid="u1")
        assert session.session_id.startswith("sess_")
        assert len(session.session_id) > 20

    def test_session_id_uniqueness(self, ctx):
        fs, db, root = ctx
        init_user(fs, db, "u1")
        ids = set()
        for _ in range(20):
            ids.add(init_session(fs, db, user_uuid="u1").session_id)
        assert len(ids) == 20

    def test_multi_user_isolation(self, ctx):
        fs, db, root = ctx
        init_user(fs, db, "u1")
        init_user(fs, db, "u2")
        init_session(fs, db, user_uuid="u1", session_id="s1")
        init_session(fs, db, user_uuid="u2", session_id="s2")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
