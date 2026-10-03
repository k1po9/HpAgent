"""Real Temporal Activity executes a blocking file tool during grant revocation."""
from __future__ import annotations

import asyncio
import os
import threading
from contextlib import suppress
from datetime import timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest
from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker
from temporalio.workflow import ActivityCancellationType

from conversation_domain.commands import CommandService
from file_domain.models import SourceLocator, TextView
from file_runtime import FileAdapterRegistry
from persistence.uow import UnitOfWork
from sandbox.tools.local.file_read import create_file_read_tools
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog
from workspace.file_scope import RunFileWorkspace
from workspace.resources import ResourceDenied, ResourcePolicy

from .test_workspace_v41_p2 import _conversation, _file, _run

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]
STATE: SimpleNamespace | None = None


@activity.defn(name="workspace_blocking_read")
async def workspace_blocking_read() -> str:
    assert STATE is not None
    async def heartbeat() -> None:
        while True:
            activity.heartbeat()
            await asyncio.sleep(0.1)

    pulse = asyncio.create_task(heartbeat())
    try:
        with STATE.workspace.prepare(STATE.account_id, STATE.run_id) as scope:
            STATE.scratch_root = scope.scratch_root
            scope.select(STATE.node_id)
            tool = {item.name: item for item in create_file_read_tools(
                lambda: scope, STATE.registry,
            )}["read_file"]
            return await tool.ainvoke({"file": "active.txt"})
    finally:
        pulse.cancel()
        with suppress(asyncio.CancelledError):
            await pulse


@workflow.defn
class WorkspaceBlockingReadWorkflow:
    @workflow.run
    async def run(self) -> str:
        return await workflow.execute_activity(
            workspace_blocking_read, start_to_close_timeout=timedelta(minutes=2),
            heartbeat_timeout=timedelta(seconds=2),
            cancellation_type=ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )


async def _wait(condition, timeout: float = 10) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


async def test_temporal_blocked_file_tool_stops_after_adapter_exit(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    global STATE
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    namespace = os.getenv("TEMPORAL_TEST_NAMESPACE", "hpagent-workspace-revoke")
    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url, commands)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    origin, reader = (_conversation(commands, account_id) for _ in range(2))
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    file_id = _file(db, store, account_id, origin, "active.txt", b"active content")
    node_id = catalog.save_file(account_id, parent, file_id, "active.txt", "save:active")
    grants = policy.grant(account_id, "conversation", reader, parent,
                          ["list_metadata", "read_content"], True)
    run_id = _run(commands, account_id, reader)
    assert CommandService(worker_database_url).start_run(account_id, run_id)
    entered = threading.Event()
    release = threading.Event()
    exited = threading.Event()

    class BlockingAdapter:
        def convert(self, resource, *, max_chars):
            entered.set()
            try:
                assert release.wait(30)
                return TextView("active content", SourceLocator(resource.file_id,
                                resource.logical_name), False, {})
            finally:
                exited.set()

    registry = FileAdapterRegistry()
    registry.register("fast_text", BlockingAdapter(), extensions=("txt",))
    execution_root = tmp_path / "execution"
    STATE = SimpleNamespace(workspace=RunFileWorkspace(worker_database_url, store,
                                                       execution_root),
                            account_id=account_id, run_id=run_id, node_id=node_id,
                            registry=registry)
    client = await Client.connect(host, namespace=namespace)
    worker = Worker(client, task_queue=f"workspace-revoke-{run_id}",
                    workflows=[WorkspaceBlockingReadWorkflow],
                    activities=[workspace_blocking_read],
                    workflow_runner=UnsandboxedWorkflowRunner())
    try:
        async with worker:
            handle = await client.start_workflow(
                WorkspaceBlockingReadWorkflow.run, id=f"workspace-revoke-{run_id}",
                task_queue=f"workspace-revoke-{run_id}",
            )
            assert await asyncio.to_thread(entered.wait, 10)
            assert STATE.scratch_root.is_relative_to(execution_root / str(account_id) / str(run_id))
            assert STATE.scratch_root.exists()
            assert policy.revoke(account_id, UUID(grants[1]), commands) == [run_id]
            with pytest.raises(ResourceDenied):
                policy.check_file(account_id, run_id, file_id)
            with UnitOfWork(database_url) as uow:
                assert uow.execute("SELECT status FROM runs WHERE run_id=%s",
                                   (run_id,)).fetchone()["status"] == "cancelling"
            await handle.cancel()
            await asyncio.sleep(1)
            assert not exited.is_set()
            assert STATE.scratch_root.exists()
            release.set()
            await asyncio.wait_for(handle.result(), 20)
            await _wait(lambda: exited.is_set() and not STATE.scratch_root.exists())
            assert commands.cancelled_run(account_id, run_id)
            with UnitOfWork(database_url) as uow:
                assert uow.execute("SELECT status FROM runs WHERE run_id=%s",
                                   (run_id,)).fetchone()["status"] == "cancelled"
    finally:
        release.set()
        STATE = None
