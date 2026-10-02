"""Production Task API, Outbox, Temporal and reconciler cancellation path."""
from __future__ import annotations

import asyncio
import os
import threading
from contextlib import suppress
from uuid import UUID, uuid4

import pytest
from support.work_fixtures import research_requirement
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.worker import Worker

from file_domain.models import SourceLocator, TextView
from file_runtime import FileAdapterRegistry
from orchestration.research_workflow import ResearchReportWorkflow
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
from orchestration.web_dispatcher import (
    TemporalClientAdapter,
    TemporalOutboxDispatcher,
    WebOutboxDispatcher,
)
from orchestration.web_reconcile_adapters import LifecycleReconcileStore
from orchestration.web_reconciler import TemporalInspectorAdapter, WebRunReconciler
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from sandbox.tools.local.file_read import create_file_read_tools
from storage.tenant_file_store import TenantFileStore
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.outbox import OutboxService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from workspace.file_scope import RunFileWorkspace
from workspace.resources import ResourceDenied, ResourcePolicy

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        headers["Idempotency-Key"] = key
    return headers


async def _until(condition, timeout: float = 30) -> None:
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.05)


@pytest.mark.parametrize("retry_after_dispatcher_restart", [False, True])
async def test_work_revoke_uses_production_cancel_chain(
    seed_identity, client_factory, db, database_url, worker_database_url, tmp_path,
    retry_after_dispatcher_restart,
):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    account_id = seed_identity("research-cancel-chain")
    store_root = tmp_path / "store"
    client_api = client_factory(file_upload_enabled=True, file_store_root=str(store_root))
    assert client_api.post("/auth/login", json={"username": "research-cancel-chain",
        "password": "correct-password"}, follow_redirects=False).status_code == 303
    csrf = client_api.get("/api/v1/me").json()["csrf_token"]
    tree = client_api.get("/api/v1/workspace").json()
    parent = next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料")
    body = b"protected research input"
    upload = client_api.post("/api/v1/workspace/uploads", json={
        "file_name": "research.txt", "size_bytes": len(body), "content_type": "text/plain",
    }, headers=_headers(csrf, str(uuid4())))
    assert upload.status_code == 201
    file_id = UUID(upload.json()["file"]["file_id"])
    assert client_api.put(upload.json()["content_url"], content=body,
        headers={**_headers(csrf), "Content-Type": "application/octet-stream"}).status_code == 200
    saved = client_api.post("/api/v1/workspace/files", json={
        "parent_id": parent, "file_id": str(file_id), "name": "research.txt",
    }, headers=_headers(csrf, str(uuid4())))
    assert saved.status_code == 201
    node_id = UUID(saved.json()["node_id"])
    work = client_api.post("/api/v1/works", json={
        "title": "Revocable Research", "requirement": research_requirement("Read the selected input"),
    }, headers=_headers(csrf, str(uuid4())))
    assert work.status_code == 201, work.text
    work_id = work.json()["work"]["work_id"]
    granted = client_api.post(f"/api/v1/works/{work_id}/resources", json={
        "node_id": parent, "operations": ["list_metadata", "read_content"],
        "recursive": True,
    }, headers=_headers(csrf))
    assert granted.status_code == 201
    read_grant = granted.json()["grant_ids"][1]
    triggered = client_api.post(f"/api/v1/works/{work_id}/advance", json={},
        headers={**_headers(csrf, str(uuid4())), "If-Match": work.headers["ETag"]})
    assert triggered.status_code == 202, triggered.text
    run_id = UUID(triggered.json()["run"]["run_id"])
    store = TenantFileStore(store_root, max_bytes=1024 * 1024)
    execution_root = tmp_path / "execution"
    workspace = RunFileWorkspace(worker_database_url, store, execution_root)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    class BlockingAdapter:
        def convert(self, resource, *, max_chars):
            entered.set()
            try:
                assert release.wait(60)
                return TextView(body.decode(), SourceLocator(resource.file_id,
                                resource.logical_name), False, {})
            finally:
                exited.set()

    registry = FileAdapterRegistry()
    registry.register("fast_text", BlockingAdapter(), extensions=("txt",))

    @activity.defn(name="fetch_research_sources_activity")
    async def blocked_fetch(request):
        async def heartbeat():
            while True:
                activity.heartbeat()
                await asyncio.sleep(0.1)
        pulse = asyncio.create_task(heartbeat())
        try:
            with workspace.prepare(account_id, run_id) as scope:
                scope.select(node_id)
                tool = {tool.name: tool for tool in create_file_read_tools(
                    lambda: scope, registry,
                )}["read_file"]
                await tool.ainvoke({"file": "research.txt"})
            return {"item_count": 1}
        finally:
            pulse.cancel()
            with suppress(asyncio.CancelledError):
                await pulse

    names = (
        "create_research_plan_activity", "discover_research_sources_activity",
        "rank_research_sources_activity", "normalize_research_sources_activity",
        "extract_research_evidence_activity", "assess_research_corroboration_activity",
        "analyze_research_gaps_activity", "synthesize_research_report_activity",
        "verify_research_citations_activity", "compare_previous_research_activity",
        "publish_research_artifact_activity", "save_research_workspace_activity",
        "complete_research_activity", "fail_research_activity",
    )

    def stub(name):
        async def stage(request):
            return {"item_count": 1, "sufficient": True}
        return activity.defn(name=name)(stage)

    research = ResearchActivities(worker_database_url, None, None, None, None)
    temporal = await Client.connect(host, namespace=os.getenv(
        "TEMPORAL_TEST_NAMESPACE", "hpagent-v41-final"))
    executions = PostgresWorkflowExecutionStore(worker_database_url)
    lifecycle = WebRunLifecycleService(worker_database_url)
    temporal_adapter = TemporalClientAdapter(temporal)

    class FailFirstCancel:
        def __init__(self):
            self.failed = False

        async def start_research_run(self, workflow_id, request):
            return await temporal_adapter.start_research_run(workflow_id, request)

        async def cancel_web_run(self, workflow_id):
            if not self.failed:
                self.failed = True
                raise RuntimeError("injected cancel dispatch failure")
            return await temporal_adapter.cancel_web_run(workflow_id)

    first_adapter = FailFirstCancel() if retry_after_dispatcher_restart else temporal_adapter
    dispatcher = WebOutboxDispatcher(OutboxService(worker_database_url),
        TemporalOutboxDispatcher(executions, first_adapter, lifecycle),
        f"research-cancel-first-{run_id}")
    reconciler = WebRunReconciler(
        LifecycleReconcileStore(executions, lifecycle), TemporalInspectorAdapter(temporal),
    )
    worker = Worker(temporal, task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[ResearchReportWorkflow],
        activities=[research.prepare_research_activity, blocked_fetch,
                    *(stub(name) for name in names)])
    stop = asyncio.Event()

    async def consume():
        while not stop.is_set():
            await dispatcher.run_once()
            await asyncio.sleep(0.05)

    try:
        async with worker:
            consumer = asyncio.create_task(consume())
            try:
                assert await asyncio.to_thread(entered.wait, 20)
                root = execution_root / str(run_id)
                assert root.exists()
                revoke_path = f"/api/v1/works/{work_id}/resources/{read_grant}"
                revoked = await asyncio.to_thread(client_api.delete, revoke_path,
                                                  headers=_headers(csrf))
                assert revoked.status_code == 200, revoked.text
                assert revoked.json() == {"affected_run_ids": [str(run_id)]}
                with UnitOfWork(database_url) as uow:
                    assert uow.execute("SELECT status FROM runs WHERE run_id=%s",
                                       (run_id,)).fetchone()["status"] == "cancelling"
                    assert uow.execute("SELECT count(*) AS n FROM outbox_events WHERE run_id=%s "
                                       "AND event_type='cancel_run'", (run_id,)).fetchone()["n"] == 1
                with pytest.raises(ResourceDenied):
                    ResourcePolicy(database_url).check_file(account_id, run_id, file_id)
                if retry_after_dispatcher_restart:
                    await _until(lambda: db.execute(
                        "SELECT last_error_code FROM outbox_events WHERE run_id=%s "
                        "AND event_type='cancel_run'", (run_id,)
                    ).fetchone()[0] == "temporal_dispatch_failed")
                    consumer.cancel()
                    with suppress(asyncio.CancelledError):
                        await consumer
                    dispatcher = WebOutboxDispatcher(OutboxService(worker_database_url),
                        TemporalOutboxDispatcher(executions, temporal_adapter, lifecycle),
                        f"research-cancel-replacement-{run_id}")
                    consumer = asyncio.create_task(consume())
                await _until(lambda: db.execute(
                    "SELECT status FROM workflow_executions WHERE run_id=%s", (run_id,)
                ).fetchone()[0] == "cancel_requested")
                assert not exited.is_set()
                assert root.exists()
                assert db.execute("SELECT status FROM runs WHERE run_id=%s",
                                  (run_id,)).fetchone()[0] == "cancelling"
                repeated = await asyncio.to_thread(client_api.delete, revoke_path,
                                                   headers=_headers(csrf))
                assert repeated.status_code == 200
                assert repeated.json() == {"affected_run_ids": []}
                await _until(lambda: db.execute(
                    "SELECT status FROM outbox_events WHERE run_id=%s "
                    "AND event_type='cancel_run'", (run_id,)
                ).fetchone()[0] == "processed")
                # Re-deliver the committed event as after an acknowledgement loss.
                db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL,"
                           "available_at=now() WHERE run_id=%s AND event_type='cancel_run'",
                           (run_id,))
                expected_attempts = 3 if retry_after_dispatcher_restart else 2

                def redelivered():
                    row = db.execute("SELECT status,attempt_count FROM outbox_events "
                                     "WHERE run_id=%s AND event_type='cancel_run'",
                                     (run_id,)).fetchone()
                    return row[0] == "processed" and row[1] == expected_attempts

                await _until(redelivered)
                assert not exited.is_set()
                release.set()
                await _until(lambda: exited.is_set() and not root.exists())
                handle = temporal.get_workflow_handle(f"hpagent-research-{run_id}")
                async with asyncio.timeout(30):
                    while (await handle.describe()).status == WorkflowExecutionStatus.RUNNING:
                        await asyncio.sleep(0.1)
                assert (await handle.describe()).status == WorkflowExecutionStatus.CANCELED
                assert db.execute("SELECT status FROM runs WHERE run_id=%s",
                                  (run_id,)).fetchone()[0] == "cancelling"
            finally:
                release.set()
                stop.set()
                consumer.cancel()
                with suppress(asyncio.CancelledError):
                    await consumer
    finally:
        release.set()
    replacement_worker = Worker(temporal, task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[ResearchReportWorkflow],
        activities=[research.prepare_research_activity, blocked_fetch,
                    *(stub(name) for name in names)])
    async with replacement_worker:
        await reconciler.run_once()
        assert db.execute("SELECT status FROM runs WHERE run_id=%s",
                          (run_id,)).fetchone()[0] == "cancelled"
        await reconciler.run_once()
    assert db.execute("SELECT count(*) FROM outbox_events WHERE run_id=%s "
                      "AND event_type='cancel_run'", (run_id,)).fetchone()[0] == 1
    assert db.execute("SELECT attempt_count FROM outbox_events "
                      "WHERE run_id=%s AND event_type='cancel_run'",
                      (run_id,)).fetchone()[0] == expected_attempts
