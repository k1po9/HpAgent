"""
Workspace provisioning regression tests (fix doc §8).

Cover the ``GitRepoManager`` provisioning lifecycle that Web Runs rely on:
missing repo / session branch are created ONLY when their absence is provably
safe; every ambiguous state fails closed with ``WorkspaceRecoveryRequired``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from sandbox.git_repo import GitRepoManager
from workspace.isolation import WorkspaceRecoveryRequired


def _manager(tmp_path: Path) -> GitRepoManager:
    return GitRepoManager(repos_root=tmp_path)


def _repo(tmp_path: Path, account_id: str = "acct-1") -> Path:
    return tmp_path / account_id / "repo"


def _git(repo: Path, *args: str) -> str:
    """Run a git command and require success; return trimmed stdout."""
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _branches(repo: Path) -> str:
    return _git(repo, "branch", "--list", "hpagent/*")


async def test_fresh_account_provisions_repo_and_session_branch(tmp_path):
    """Test 1: fresh Account + first Web Conversation provisions everything."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")

    repo = _repo(tmp_path)
    assert (repo / ".git").exists()
    # Empty root commit exists (exactly one — provisioning, no re-init noise).
    assert _git(repo, "rev-list", "--count", "HEAD") == "1"
    # The Run's expected branch is checked out.
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-A"


async def test_same_conversation_second_message_reuses_repo_and_branch(tmp_path):
    """Test 2: provisioning is idempotent — no re-init, no duplicate branch."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    await mgr.ensure_session_workspace("acct-1", "session-A")

    repo = _repo(tmp_path)
    assert _git(repo, "rev-list", "--count", "HEAD") == "1"  # root commit only
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-A"


async def test_new_conversation_creates_new_session_branch_in_same_repo(tmp_path):
    """Test 3: session_B gets its own branch; session_A is preserved."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    await mgr.ensure_session_workspace("acct-1", "session-B")

    repo = _repo(tmp_path)
    branches = _branches(repo)
    assert "hpagent/session-A" in branches
    assert "hpagent/session-B" in branches
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-B"


async def test_existing_expected_branch_is_not_recreated(tmp_path):
    """Test 4: an existing branch is left for recovery, never recreated."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    repo = _repo(tmp_path)
    head_before = _git(repo, "rev-parse", "HEAD")

    await mgr.ensure_session_workspace("acct-1", "session-A")

    assert _git(repo, "rev-parse", "HEAD") == head_before


async def test_clean_other_session_branch_yields_to_new_branch(tmp_path):
    """Test 5: clean session_A → provisioning may create session_B safely."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    repo = _repo(tmp_path)
    (repo / "note.txt").write_text("A", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "A")

    await mgr.ensure_session_workspace("acct-1", "session-B")

    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-B"
    # session_A content is preserved, reachable on its own branch.
    _git(repo, "checkout", "hpagent/session-A")
    assert (repo / "note.txt").read_text(encoding="utf-8") == "A"


async def test_dirty_other_session_branch_fails_closed(tmp_path):
    """Test 6: dirty session_A must NOT be stashed/reset/force-switched."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    repo = _repo(tmp_path)
    (repo / "dirty.txt").write_text("must remain", encoding="utf-8")  # untracked → dirty

    with pytest.raises(WorkspaceRecoveryRequired):
        await mgr.ensure_session_workspace("acct-1", "session-B")

    assert (repo / "dirty.txt").read_text(encoding="utf-8") == "must remain"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-A"


async def test_unfinished_git_operation_fails_closed(tmp_path):
    """Test 7: MERGE_HEAD/REBASE_HEAD/CHERRY_PICK_HEAD are never swallowed."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    repo = _repo(tmp_path)
    git_dir = repo / ".git"
    (git_dir / "MERGE_HEAD").write_text("deadbeef", encoding="utf-8")

    with pytest.raises(WorkspaceRecoveryRequired):
        await mgr.ensure_session_workspace("acct-1", "session-B")

    assert (git_dir / "MERGE_HEAD").exists()  # not cleared


async def test_unknown_index_lock_fails_closed(tmp_path):
    """Test 8: an unproven index.lock blocks provisioning."""
    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")
    repo = _repo(tmp_path)
    (repo / ".git" / "index.lock").write_text("unknown owner", encoding="utf-8")

    with pytest.raises(WorkspaceRecoveryRequired):
        await mgr.ensure_session_workspace("acct-1", "session-B")


async def test_non_git_non_empty_repo_dir_is_refused(tmp_path):
    """Test 9: an existing non-empty directory is never adopted via git init."""
    mgr = _manager(tmp_path)
    repo = _repo(tmp_path)
    repo.mkdir(parents=True)
    (repo / "foo.txt").write_text("not a repo", encoding="utf-8")

    with pytest.raises(WorkspaceRecoveryRequired):
        await mgr.ensure_session_workspace("acct-1", "session-A")

    assert not (repo / ".git").exists()
    assert (repo / "foo.txt").read_text(encoding="utf-8") == "not a repo"


async def test_existing_valid_repo_is_not_reinitialized(tmp_path):
    """Existing repo with its own history is left intact; branch is added."""
    repo = _repo(tmp_path)
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "existing.txt").write_text("pre-existing", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "existing")
    root_commit = _git(repo, "rev-parse", "HEAD")

    mgr = _manager(tmp_path)
    await mgr.ensure_session_workspace("acct-1", "session-A")

    assert _git(repo, "rev-parse", "HEAD") == root_commit
    assert (repo / "existing.txt").read_text(encoding="utf-8") == "pre-existing"
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-A"


async def test_qq_legacy_lifecycle_still_works(tmp_path):
    """Test 10: the QQ ``ensure_repo`` + ``start_session`` path is unchanged."""
    mgr = _manager(tmp_path)
    await mgr.ensure_repo("acct-1")
    branch = await mgr.start_session("acct-1", "session-qq-1")
    assert branch == "hpagent/session-qq-1"
    repo = _repo(tmp_path)
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == "hpagent/session-qq-1"
    # Second QQ session on the same account: repo reused, new branch created.
    await mgr.start_session("acct-1", "session-qq-2")
    assert "hpagent/session-qq-1" in _branches(repo)
    assert "hpagent/session-qq-2" in _branches(repo)
