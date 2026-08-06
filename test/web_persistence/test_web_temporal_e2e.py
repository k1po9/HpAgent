from __future__ import annotations

import asyncio
import os
import subprocess
from contextlib import AsyncExitStack

import pytest
from temporalio.client import Client

from agent_execution.facade import AgentExecutionFacade, ExecutionResult
from agent_execution.web_adapters import (
    LifecycleWebReplySink,
    PostgresWebRequestLoader,
    TemporalActivityControl,
)
from agent_execution.web_events import RedisWebRunEventSinkFactory
from agent_execution.web_host import WebExecutionHost
from application.context_assembly import ContextAssemblyService
from harness.context_builder import HarnessContextBuilder
from orchestration.web_activities import (
    execute_agent_activity,
    finalize_cancelled_activity,
    finalize_failed_activity,
    inject_web_execution_host,
    inject_web_lifecycle,
    prepare_run_activity,
)
from orchestration.web_dispatcher import TemporalClientAdapter
from orchestration.web_workers import build_web_temporal_workers
from orchestration.web_workflow import WebRunWorkflowInput
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from workspace.isolation import (
    AccountLockRegistry,
    SessionResourceRecoveryService,
)

from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.temporal]


def _provision_workspace(workspace_root, account_id, session_id) -> None:
    """Create the real per-Account git worktree the resource-prep service recovers.

    The production ``WorkspaceRecoveryGuard`` verifies the repo is on branch
    ``hpagent/{session_id}`` and clean; a fresh repo on that branch with one
    commit satisfies it without discarding anything.
    """
    repo_path = workspace_root / str(account_id) / "repo"
    repo_path.mkdir(parents=True)
    for args in (
        ["git", "init", "-b", f"hpagent/{session_id}"],
        ["git", "config", "user.email", "hpagent@test"],
        ["git", "config", "user.name", "hpagent-test"],
    ):
        subprocess.run(args, cwd=repo_path, check=True, capture_output=True)
    (repo_path / "README.md").write_text("# workspace", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_path, check=True, capture_output=True)


class _CannedBrainLoop:
    """The only stubbed component: real models/credentials are not available in E2E.

    Everything around it is the real production composition: PostgreSQL loader,
    ContextAssembly, Account-lock + workspace recovery + sandbox creation,
    Redis event projection (degraded), Temporal Activity control and the
    Lifecycle ReplySink.
    """

    async def execute(self, request, control, events, audit):
        await events.progress("generating", "producing canned reply")
        return ExecutionResult("real temporal reply", 0)


class _RecordingSandboxManager:
    def __init__(self, calls: list[tuple[str, str]]):
        self._calls = calls

    def create_session_sandbox(
        self, session_id, workspace_path, user_uuid="", session_context=None
    ):
        self._calls.append((str(session_id), workspace_path))
        return f"sandbox-{session_id}"


async def test_td_002_real_db_prepare_backfills_lost_start_ack(
    db, account_id, database_url, worker_database_url, tmp_path
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")

    _, _, run_id = _conversation_and_run(database_url, account_id)
    lifecycle = WebRunLifecycleService(worker_database_url)

    with db.cursor() as cursor:
        cursor.execute(
            "SELECT session_id FROM runs WHERE run_id=%s", (run_id,)
        )
        session_id = cursor.fetchone()[0]
    _provision_workspace(tmp_path, account_id, session_id)

    sandbox_calls: list[tuple[str, str]] = []
    context = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    host = WebExecutionHost(
        PostgresWebRequestLoader(worker_database_url, context),
        AgentExecutionFacade(_CannedBrainLoop()),
        RedisWebRunEventSinkFactory(None),
        LifecycleWebReplySink(lifecycle),
        TemporalActivityControl(),
        resource_prep=SessionResourceRecoveryService(
            worker_database_url,
            tmp_path,
            _RecordingSandboxManager(sandbox_calls),
            AccountLockRegistry(),
        ),
    )

    inject_web_lifecycle(lifecycle)
    inject_web_execution_host(host)
    client = await Client.connect(temporal_host)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            prepare_run_activity,
            finalize_failed_activity,
            finalize_cancelled_activity,
        ],
        agent_activities=[execute_agent_activity],
    )
    store = PostgresWorkflowExecutionStore(worker_database_url)
    decision = await asyncio.to_thread(store.prepare_start, run_id)
    assert decision.should_start is True

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(workers.lifecycle)
        await stack.enter_async_context(workers.agent)
        temporal_run_id = await TemporalClientAdapter(client).start_web_run(
            decision.workflow_id, WebRunWorkflowInput(1, str(run_id))
        )
        result = await client.get_workflow_handle(
            decision.workflow_id, run_id=temporal_run_id
        ).result()

    assert result["outcome"] == "completed"
    assert db.execute(
        "SELECT status FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == "completed"
    execution = db.execute(
        "SELECT temporal_run_id,status FROM workflow_executions WHERE run_id=%s AND is_current",
        (run_id,),
    ).fetchone()
    assert str(execution[0]) == temporal_run_id
    assert execution[1] == "running"
    assert db.execute(
        "SELECT status,content FROM messages WHERE produced_by_run_id=%s", (run_id,)
    ).fetchone() == ("completed", "real temporal reply")

    # P0/P1-3: the real production host chain ran — the per-Account workspace was
    # recovered under the Account execution lock and the Session Sandbox created
    # before the Facade executed.  No CompletingHost stub is involved.
    assert sandbox_calls == [
        (str(session_id), str(tmp_path / str(account_id) / "repo"))
    ]
