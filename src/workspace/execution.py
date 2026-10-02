"""Ephemeral Execution resources; Git is not a generic execution prerequisite."""

from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_activities.fencing import execution_fence
from agent_activities.store import AgentDataStore
from persistence.uow import UnitOfWork


class ExecutionResourceService:
    def __init__(self, database, sandbox_manager, run_file_workspace=None, *, execution_root=None):
        self.database = database
        self.sandbox_manager = sandbox_manager
        self.run_file_workspace = run_file_workspace
        self.execution_root = Path(execution_root) if execution_root else None

    @asynccontextmanager
    async def lease_for_run(self, account_id, run_id, control=None):
        with UnitOfWork(self.database) as uow:
            AgentDataStore._assert_fence(uow)
            run = AgentDataStore._active_run(uow, str(account_id), str(run_id))
            execution = AgentDataStore._execution(uow, str(account_id), str(run_id))
        execution_id = str(execution["execution_id"])
        fence = execution_fence.get()
        if fence is None or fence[2] != execution_id:
            raise ValueError("Execution resources require an active attempt fence")
        if control is not None:
            control.raise_if_cancelled()
        resource_key = f"{execution_id}:{fence[3]}"
        bound = False
        sandbox_id = None
        try:
            if self.run_file_workspace is not None:
                with self.run_file_workspace.prepare(account_id, run_id) as scope:
                    self.sandbox_manager.bind_run_file_scope(resource_key, resource_key, scope)
                    bound = True
                    sandbox_id = self._sandbox(
                        account_id, run_id, resource_key, scope.scratch_root, run
                    )
                    yield execution_id
            else:
                root = (
                    self.execution_root
                    / str(account_id)
                    / str(run_id)
                    / execution_id
                    / str(fence[3])
                )
                root.mkdir(parents=True, exist_ok=True, mode=0o700)
                with TemporaryDirectory(dir=root) as scratch:
                    sandbox_id = self._sandbox(account_id, run_id, resource_key, scratch, run)
                    yield execution_id
        finally:
            if sandbox_id is not None:
                self.sandbox_manager.destroy_sandbox(sandbox_id)
            if bound:
                self.sandbox_manager.unbind_run_file_scope(resource_key)

    def _sandbox(self, account_id, run_id, execution_id, scratch, run):
        with UnitOfWork(self.database) as uow:
            AgentDataStore._assert_fence(uow)
            origin = uow.execute(
                "SELECT origin FROM messages WHERE message_id=%s", (run["trigger_message_id"],)
            ).fetchone()
        return self.sandbox_manager.create_execution_sandbox(
            execution_id=execution_id,
            workspace_path=str(scratch),
            user_uuid=str(account_id),
            session_context={
                "account_id": str(account_id),
                "source_kind": run["source_kind"],
                "conversation_id": str(run["conversation_id"]) if run["conversation_id"] else None,
                "trigger_message_id": str(run["trigger_message_id"]) if run["trigger_message_id"] else None,
                "channel_type": (origin["origin"] if origin else {}).get("channel_type", "web"),
                "metadata": {"run_id": str(run_id), "execution_id": execution_id},
            },
        )
