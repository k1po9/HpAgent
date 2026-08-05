from __future__ import annotations

import asyncio

import pytest

from workspace.isolation import (
    AccountLockRegistry,
    WorkspaceIsolationConfigurationError,
    WorkspaceRecoveryGuard,
    WorkspaceRecoveryRequired,
    validate_workspace_topology,
)


def test_missing_or_unsafe_single_process_topology_is_rejected():
    with pytest.raises(WorkspaceIsolationConfigurationError):
        validate_workspace_topology(
            mode="", agent_worker_replicas=1, prefork_enabled=False,
            agent_activity_processes=1, hosts_share_lock_registry=True,
        )
    with pytest.raises(WorkspaceIsolationConfigurationError):
        validate_workspace_topology(
            mode="single_process_account_lock", agent_worker_replicas=2,
            prefork_enabled=False, agent_activity_processes=1, hosts_share_lock_registry=True,
        )


@pytest.mark.asyncio
async def test_same_account_is_serial_and_different_accounts_are_parallel():
    registry = AccountLockRegistry()
    entered: list[str] = []
    release = asyncio.Event()

    async def first():
        async with registry.hold("same"):
            entered.append("first")
            await release.wait()

    async def second():
        async with registry.hold("same"):
            entered.append("second")

    one = asyncio.create_task(first())
    await asyncio.sleep(0)
    two = asyncio.create_task(second())
    async with registry.hold("other"):
        entered.append("other")
    assert entered == ["first", "other"]
    release.set()
    await asyncio.gather(one, two)
    assert entered == ["first", "other", "second"]


@pytest.mark.asyncio
async def test_recovery_preserves_unknown_dirty_state(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "kept.txt").write_text("must remain", encoding="utf-8")
    guard = WorkspaceRecoveryGuard(tmp_path)

    async def fake_git(*args: str) -> str:
        return "other-session" if args[0] == "rev-parse" else "?? kept.txt"

    guard._git = fake_git  # type: ignore[method-assign]
    with pytest.raises(WorkspaceRecoveryRequired):
        await guard.recover("expected-session")
    assert (tmp_path / "kept.txt").read_text(encoding="utf-8") == "must remain"
