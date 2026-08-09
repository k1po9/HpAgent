"""
End-to-end workspace provisioning through ``SessionResourceRecoveryService``.

These are the Web Session provisioning lifecycle tests from fix doc §8 (Test 1-3):
the local repo / session branch that ``lease_for_run`` used to assume existed are
now provisioned safely under the Account lock.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from sandbox.git_repo import GitRepoManager
from workspace.isolation import (
    AccountLockRegistry,
    SessionResourceRecoveryService,
)

from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


class _RecordingSandboxManager:
    def __init__(self, calls: list[tuple[str, str]]):
        self._calls = calls

    def create_session_sandbox(
        self, session_id, workspace_path, user_uuid="", session_context=None
    ):
        self._calls.append((str(session_id), workspace_path))
        return f"sandbox-{session_id}"


def _git(repo, *args: str) -> str:
    import subprocess

    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


async def test_fresh_account_first_conversation_provisions_workspace(
    db, account_id, database_url, worker_database_url, tmp_path
):
    """Test 1: repo does not exist → provisioned before the Run can execute."""
    _, _, run_id = _conversation_and_run(database_url, account_id)
    session_id = db.execute(
        "SELECT session_id FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()[0]

    sandbox_calls: list[tuple[str, str]] = []
    service = SessionResourceRecoveryService(
        worker_database_url,
        _RecordingSandboxManager(sandbox_calls),
        AccountLockRegistry(),
        GitRepoManager(tmp_path),
    )

    async with service.lease_for_run(account_id, run_id) as leased:
        assert leased == session_id

    repo = tmp_path / str(account_id) / "repo"
    assert (repo / ".git").exists()
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == f"hpagent/{session_id}"
    assert sandbox_calls == [
        (str(session_id), str(repo))
    ]


async def test_same_conversation_second_message_reuses_session(
    db, account_id, database_url, worker_database_url, tmp_path
):
    """Test 2: a second Run in the same Conversation reuses repo + branch."""
    svc, conv, run_1 = _conversation_and_run(database_url, account_id)
    svc.start_run(account_id, run_1)
    session_1 = db.execute(
        "SELECT session_id FROM runs WHERE run_id=%s", (run_1,)
    ).fetchone()[0]

    sandbox_calls: list[tuple[str, str]] = []
    lease = SessionResourceRecoveryService(
        worker_database_url,
        _RecordingSandboxManager(sandbox_calls),
        AccountLockRegistry(),
        GitRepoManager(tmp_path),
    )
    async with lease.lease_for_run(account_id, run_1):
        pass
    svc.complete_run(account_id, run_1, "done")

    run_2 = UUID(
        svc.send_message(account_id, conv, str(uuid4()), "second message")["run_id"]
    )
    svc.start_run(account_id, run_2)
    session_2 = db.execute(
        "SELECT session_id FROM runs WHERE run_id=%s", (run_2,)
    ).fetchone()[0]
    assert session_2 == session_1  # same Conversation → same Web Session

    repo = tmp_path / str(account_id) / "repo"
    head_before = _git(repo, "rev-parse", "HEAD")
    async with lease.lease_for_run(account_id, run_2):
        pass

    # No re-init, no new branch, no extra commit.
    assert _git(repo, "rev-parse", "HEAD") == head_before
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == f"hpagent/{session_2}"
    assert _git(repo, "rev-list", "--count", "HEAD") == "1"


async def test_new_conversation_creates_new_session_branch(
    db, account_id, database_url, worker_database_url, tmp_path
):
    """Test 3: a new Conversation gets its own Session branch in the same repo."""
    svc, conv_a, run_a = _conversation_and_run(database_url, account_id)
    svc.start_run(account_id, run_a)
    session_a = db.execute(
        "SELECT session_id FROM runs WHERE run_id=%s", (run_a,)
    ).fetchone()[0]

    sandbox_calls: list[tuple[str, str]] = []
    lease = SessionResourceRecoveryService(
        worker_database_url,
        _RecordingSandboxManager(sandbox_calls),
        AccountLockRegistry(),
        GitRepoManager(tmp_path),
    )
    async with lease.lease_for_run(account_id, run_a):
        pass
    svc.complete_run(account_id, run_a, "done")

    conv_b = UUID(
        svc.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    run_b = UUID(
        svc.send_message(account_id, conv_b, str(uuid4()), "first in B")["run_id"]
    )
    svc.start_run(account_id, run_b)
    session_b = db.execute(
        "SELECT session_id FROM runs WHERE run_id=%s", (run_b,)
    ).fetchone()[0]
    assert session_b != session_a

    repo = tmp_path / str(account_id) / "repo"
    async with lease.lease_for_run(account_id, run_b):
        pass

    branches = _git(repo, "branch", "--list", "hpagent/*")
    assert f"hpagent/{session_a}" in branches
    assert f"hpagent/{session_b}" in branches
    assert _git(repo, "rev-parse", "--abbrev-ref", "HEAD") == f"hpagent/{session_b}"
