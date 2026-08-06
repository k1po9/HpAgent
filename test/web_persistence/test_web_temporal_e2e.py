from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack

import pytest
from temporalio.client import Client

from agent_execution.facade import ExecutionResult
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

from .test_phase_a_invariants import _conversation_and_run

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.temporal]


async def test_td_002_real_db_prepare_backfills_lost_start_ack(
    db, account_id, database_url, worker_database_url
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")

    _, _, run_id = _conversation_and_run(database_url, account_id)
    lifecycle = WebRunLifecycleService(worker_database_url)

    class CompletingHost:
        async def execute(self, value):
            assert value == str(run_id)
            await asyncio.to_thread(lifecycle.complete, run_id, "real temporal reply")
            return ExecutionResult("real temporal reply", 0)

    inject_web_lifecycle(lifecycle)
    inject_web_execution_host(CompletingHost())
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
