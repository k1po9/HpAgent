"""Hard startup checks, account execution locks, and fail-closed recovery."""
from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import asynccontextmanager
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Protocol
from uuid import UUID

from persistence.repositories import RunRepository
from persistence.uow import UnitOfWork


class WorkspaceIsolationMode(StrEnum):
    SINGLE_PROCESS_ACCOUNT_LOCK = "single_process_account_lock"
    SESSION_WORKTREE = "session_worktree"


class WorkspaceIsolationConfigurationError(RuntimeError):
    pass


class WorkspaceRecoveryRequired(RuntimeError):
    """Recovery cannot prove it will preserve the existing workspace state."""

    code = "workspace_recovery_required"


class WorkspaceProvisioner(Protocol):
    """Worker-owned safe creation of missing workspace resources.

    ``GitRepoManager`` implements this (see ``sandbox.git_repo``); the Protocol
    keeps ``workspace.isolation`` free of a ``sandbox.git_repo`` import so the
    two modules never form a cycle.
    """

    def repo_path(self, account_id: str) -> Path: ...

    async def ensure_session_workspace(self, account_id: str, session_id: str) -> None: ...


class ExecutionControl(Protocol):
    def raise_if_cancelled(self) -> None: ...

    async def heartbeat(self, phase: str, **safe_details: Any) -> None: ...


class AccountLockRegistry:
    """Shared in-process Account execution locks.

    This is intentionally a runtime dependency, not a module-level singleton:
    composition roots must inject the same instance into QQ and Web hosts.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def _lock_for(self, account_id: str) -> asyncio.Lock:
        async with self._guard:
            return self._locks.setdefault(account_id, asyncio.Lock())

    @asynccontextmanager
    async def hold(
        self,
        account_id: str,
        control: ExecutionControl | None = None,
        *,
        heartbeat_seconds: float = 15.0,
    ) -> AsyncIterator[None]:
        lock = await self._lock_for(account_id)
        acquired = False
        try:
            while not acquired:
                if control is not None:
                    control.raise_if_cancelled()
                try:
                    await asyncio.wait_for(lock.acquire(), timeout=heartbeat_seconds)
                    acquired = True
                except TimeoutError:
                    if control is not None:
                        await control.heartbeat("waiting_for_workspace_lock")
            yield
        finally:
            if acquired:
                lock.release()


class _ProcessFileLock:
    """One Agent Worker process per shared-worktree topology."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle: Any | None = None

    def acquire(self) -> None:
        try:
            import fcntl
        except ImportError as exc:  # pragma: no cover - supported deployment is Linux
            raise WorkspaceIsolationConfigurationError("OS process locks are unavailable") from exc
        self._path.parent.mkdir(parents=True, exist_ok=True)
        handle = self._path.open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            handle.close()
            raise WorkspaceIsolationConfigurationError(
                "another Agent Worker already owns the workspace process lock"
            ) from exc
        handle.seek(0)
        handle.truncate()
        handle.write(str(os.getpid()))
        handle.flush()
        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        import fcntl

        fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        self._handle.close()
        self._handle = None


def validate_workspace_topology(
    *, mode: str, agent_worker_replicas: int, prefork_enabled: bool,
    agent_activity_processes: int, hosts_share_lock_registry: bool,
) -> WorkspaceIsolationMode:
    try:
        selected = WorkspaceIsolationMode(mode)
    except ValueError as exc:
        raise WorkspaceIsolationConfigurationError(
            "workspace_isolation_mode must be explicitly configured"
        ) from exc
    if selected is WorkspaceIsolationMode.SINGLE_PROCESS_ACCOUNT_LOCK:
        if agent_worker_replicas != 1 or prefork_enabled or agent_activity_processes != 1:
            raise WorkspaceIsolationConfigurationError(
                "single_process_account_lock requires one non-prefork Agent Worker process"
            )
        if not hosts_share_lock_registry:
            raise WorkspaceIsolationConfigurationError(
                "QQ and Web Agent hosts must share one AccountLockRegistry"
            )
    return selected


class WorkspaceIsolationRuntime:
    """Worker-owned isolation infrastructure, created before Agent dependencies."""

    def __init__(
        self, *, workspace_root: Path, mode: str, agent_worker_replicas: int = 1,
        prefork_enabled: bool = False, agent_activity_processes: int = 1,
        hosts_share_lock_registry: bool = True,
    ) -> None:
        self.mode = validate_workspace_topology(
            mode=mode,
            agent_worker_replicas=agent_worker_replicas,
            prefork_enabled=prefork_enabled,
            agent_activity_processes=agent_activity_processes,
            hosts_share_lock_registry=hosts_share_lock_registry,
        )
        self.account_locks = AccountLockRegistry()
        self._process_lock = _ProcessFileLock(
            workspace_root / ".hpagent-agent-worker.lock"
        )

    def start(self) -> None:
        if self.mode is WorkspaceIsolationMode.SINGLE_PROCESS_ACCOUNT_LOCK:
            self._process_lock.acquire()

    def close(self) -> None:
        self._process_lock.release()


class WorkspaceRecoveryGuard:
    """Conservative shared-worktree recovery; it never resets or discards files."""

    _IN_PROGRESS = ("MERGE_HEAD", "REBASE_HEAD", "CHERRY_PICK_HEAD")

    def __init__(
        self, repo_path: Path, *, stale_lock_is_safe: Callable[[Path], bool] | None = None
    ) -> None:
        self._repo_path = repo_path
        self._stale_lock_is_safe = stale_lock_is_safe or (lambda _path: False)

    async def recover(self, expected_branch: str) -> None:
        """Verify state and switch only when preservation is provably safe."""
        git_dir = self._git_dir()
        index_lock = git_dir / "index.lock"
        if index_lock.exists():
            if not self._stale_lock_is_safe(index_lock):
                raise WorkspaceRecoveryRequired("unproven Git index.lock ownership")
            index_lock.unlink()
        if any((git_dir / marker).exists() for marker in self._IN_PROGRESS):
            raise WorkspaceRecoveryRequired("unfinished Git operation")

        branch = await self._git("rev-parse", "--abbrev-ref", "HEAD")
        dirty = await self._git("status", "--porcelain")
        if branch != expected_branch and dirty:
            raise WorkspaceRecoveryRequired("dirty state belongs to another or unknown Session")
        if branch != expected_branch:
            await self._git("checkout", expected_branch)

    def _git_dir(self) -> Path:
        candidate = self._repo_path / ".git"
        if candidate.is_dir():
            return candidate
        if candidate.is_file():
            text = candidate.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir: "):
                return (self._repo_path / text.removeprefix("gitdir: ")).resolve()
        raise WorkspaceRecoveryRequired("workspace is not a valid Git worktree")

    async def _git(self, *args: str) -> str:
        def run() -> str:
            result = subprocess.run(
                ["git", "-C", str(self._repo_path), *args],
                capture_output=True, text=True, check=False,
            )
            if result.returncode:
                raise WorkspaceRecoveryRequired(result.stderr.strip() or "Git check failed")
            return result.stdout.strip()
        return await asyncio.to_thread(run)


class SessionResourceRecoveryService:
    """Restore exactly the Session already bound to a Web Run.

    It does not create a database Session.  The caller supplies only ``run_id``;
    the Session, Account and logical workspace reference are loaded through the
    authoritative database relationship before any local resource is touched.
    """

    def __init__(
        self, database_url: object, sandbox_manager: Any,
        account_locks: AccountLockRegistry, git_repo_manager: WorkspaceProvisioner,
    ) -> None:
        self._database_url = database_url
        self._sandbox_manager = sandbox_manager
        self._account_locks = account_locks
        self._git_repo_manager = git_repo_manager
        self._runs = RunRepository()

    @asynccontextmanager
    async def lease_for_run(
        self, account_id: UUID, run_id: UUID, control: ExecutionControl | None = None
    ) -> AsyncIterator[UUID]:
        with UnitOfWork(self._database_url) as uow:
            subject = self._runs.context_subject(uow, account_id, run_id)
        if subject is None:
            raise WorkspaceRecoveryRequired("run ownership chain is unavailable")
        if subject["workspace_ref"] != "account_repo":
            raise WorkspaceRecoveryRequired("unknown logical workspace reference")

        session_id = subject["session_id"]
        account_text = str(subject["account_id"])
        # The provisioner owns "where the repo lives", so the recovery guard
        # verifies exactly the repo that was (possibly) just created.
        repo_path = self._git_repo_manager.repo_path(account_text)
        expected_branch = f"hpagent/{session_id}"
        async with self._account_locks.hold(account_text, control):
            # Provision missing resources INSIDE the Account lock: QQ and Web
            # share one registry, so no other execution can race checkout or
            # branch creation on the same account repo.  Provisioning only
            # creates provably-absent state; the conservative guard below still
            # owns verification and fail-closed recovery of existing state.
            await self._git_repo_manager.ensure_session_workspace(
                account_text, str(session_id)
            )
            await WorkspaceRecoveryGuard(repo_path).recover(expected_branch)
            self._sandbox_manager.create_session_sandbox(
                session_id=str(session_id),
                workspace_path=str(repo_path),
                user_uuid=account_text,
                session_context={"account_id": account_text, "channel_type": "web", "metadata": {}},
            )
            yield session_id
