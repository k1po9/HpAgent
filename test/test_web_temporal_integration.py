from __future__ import annotations

import asyncio
import multiprocessing
import os
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowExecutionStatus, WorkflowFailureError
from temporalio.worker import Replayer, Worker

from orchestration.web_dispatcher import TemporalClientAdapter, web_workflow_id
from orchestration.web_workers import build_web_temporal_workers
from orchestration.web_workflow import (
    WEB_AGENT_TASK_QUEUE,
    WEB_LIFECYCLE_TASK_QUEUE,
    FailureInput,
    WebRunWorkflow,
    WebRunWorkflowInput,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]


@activity.defn(name="prepare_run_activity")
async def integration_prepare(request: WebRunWorkflowInput) -> dict[str, str]:
    return {"run_id": request.run_id, "status": integration_state["prepare_status"]}


@activity.defn(name="execute_agent_activity")
async def integration_execute(request: WebRunWorkflowInput) -> dict[str, str]:
    integration_execute.calls.append(request.run_id)
    if integration_state["agent_fails"]:
        raise RuntimeError("injected Agent failure")
    if integration_state["agent_blocks"]:
        integration_state["agent_started"].set()
        try:
            cancellation = asyncio.create_task(activity.wait_for_cancelled())
            while not cancellation.done():
                activity.heartbeat({"schema_version": 1, "phase": "generating"})
                await asyncio.wait({cancellation}, timeout=0.05)
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            integration_state["agent_cancelled"] = True
            raise
    return {"run_id": request.run_id, "status": "completed"}


integration_execute.calls = []


@activity.defn(name="finalize_failed_activity")
async def integration_finalize_failed(request: FailureInput) -> dict[str, str]:
    integration_state["failed_finalized"] = True
    return {"run_id": request.run_id, "status": integration_state["failure_status"]}


@activity.defn(name="finalize_cancelled_activity")
async def integration_finalize_cancelled(request: WebRunWorkflowInput) -> dict[str, str]:
    integration_state["cancel_finalized"] = True
    return {"run_id": request.run_id, "status": integration_state["cancel_status"]}


integration_state = {}


def reset_integration_state() -> None:
    integration_state.clear()
    integration_state.update({
        "prepare_status": "running",
        "agent_blocks": False,
        "agent_fails": False,
        "agent_started": asyncio.Event(),
        "agent_cancelled": False,
        "failed_finalized": False,
        "failure_status": "failed",
        "cancel_finalized": False,
        "cancel_status": "cancelled",
    })


def _run_crash_agent_worker(temporal_host: str, started_marker: str) -> None:
    """Separate process used to prove a lost Agent Worker is not retried."""

    async def run() -> None:
        @activity.defn(name="execute_agent_activity")
        async def crash_execute(request: WebRunWorkflowInput) -> dict[str, str]:
            Path(started_marker).touch()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        client = await Client.connect(temporal_host)
        worker = Worker(
            client,
            task_queue=WEB_AGENT_TASK_QUEUE,
            activities=[crash_execute],
        )
        await worker.run()

    asyncio.run(run())


async def test_td_001_real_temporal_duplicate_start_executes_agent_once():
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")

    client = await Client.connect(temporal_host)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            integration_prepare,
            integration_finalize_failed,
            integration_finalize_cancelled,
        ],
        agent_activities=[integration_execute],
    )
    run_id = str(uuid4())
    workflow_id = web_workflow_id(run_id)
    request = WebRunWorkflowInput(1, run_id)
    integration_execute.calls.clear()
    reset_integration_state()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(workers.lifecycle)
        await stack.enter_async_context(workers.agent)
        adapter = TemporalClientAdapter(client)
        temporal_run_id = await adapter.start_web_run(workflow_id, request)
        result = await client.get_workflow_handle(
            workflow_id, run_id=temporal_run_id
        ).result()
        history = await client.get_workflow_handle(
            workflow_id, run_id=temporal_run_id
        ).fetch_history()
        await Replayer(workflows=[WebRunWorkflow]).replay_workflow(history)
        recovered_run_id = await adapter.start_web_run(workflow_id, request)

    assert result == {"schema_version": 1, "run_id": run_id, "outcome": "completed"}
    assert recovered_run_id == temporal_run_id
    assert integration_execute.calls == [run_id]


async def test_td_005_agent_worker_sigkill_heartbeat_timeout_fails_without_retry(
    tmp_path
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")

    reset_integration_state()
    client = await Client.connect(temporal_host)
    lifecycle_worker = Worker(
        client,
        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[WebRunWorkflow],
        activities=[
            integration_prepare,
            integration_finalize_failed,
            integration_finalize_cancelled,
        ],
    )
    marker = tmp_path / "agent-started"
    process = multiprocessing.get_context("spawn").Process(
        target=_run_crash_agent_worker,
        args=(temporal_host, str(marker)),
    )
    run_id = str(uuid4())
    workflow_id = web_workflow_id(run_id)
    process.start()
    try:
        async with lifecycle_worker:
            temporal_run_id = await TemporalClientAdapter(client).start_web_run(
                workflow_id, WebRunWorkflowInput(1, run_id)
            )
            handle = client.get_workflow_handle(
                workflow_id, run_id=temporal_run_id
            )
            for _ in range(200):
                if marker.exists():
                    break
                await asyncio.sleep(0.05)
            assert marker.exists(), "Agent Activity was not picked up by child Worker"
            process.kill()
            await asyncio.to_thread(process.join, 10)
            assert process.exitcode is not None
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), timeout=60)
            history = await handle.fetch_history()
            description = await handle.describe()
    finally:
        if process.is_alive():
            process.kill()
        await asyncio.to_thread(process.join, 10)

    agent_scheduled_ids = {
        event.event_id
        for event in history.events
        if event.HasField("activity_task_scheduled_event_attributes")
        and event.activity_task_scheduled_event_attributes.activity_type.name
        == "execute_agent_activity"
    }
    activity_starts = [
        event.activity_task_started_event_attributes
        for event in history.events
        if event.HasField("activity_task_started_event_attributes")
        and event.activity_task_started_event_attributes.scheduled_event_id
        in agent_scheduled_ids
    ]
    assert integration_state["failed_finalized"] is True
    assert description.status == WorkflowExecutionStatus.FAILED
    assert len(activity_starts) == 1
    assert activity_starts[0].attempt == 1


@pytest.mark.parametrize(
    ("failure_status", "expected_temporal_status"),
    [
        ("completed", WorkflowExecutionStatus.COMPLETED),
        ("failed", WorkflowExecutionStatus.FAILED),
    ],
)
async def test_td_006_td_019_real_temporal_failure_uses_domain_truth_without_retry(
    failure_status: str, expected_temporal_status: WorkflowExecutionStatus
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")

    client = await Client.connect(temporal_host)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            integration_prepare,
            integration_finalize_failed,
            integration_finalize_cancelled,
        ],
        agent_activities=[integration_execute],
    )
    run_id = str(uuid4())
    workflow_id = web_workflow_id(run_id)
    reset_integration_state()
    integration_state["agent_fails"] = True
    integration_state["failure_status"] = failure_status
    integration_execute.calls.clear()

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(workers.lifecycle)
        await stack.enter_async_context(workers.agent)
        adapter = TemporalClientAdapter(client)
        temporal_run_id = await adapter.start_web_run(
            workflow_id, WebRunWorkflowInput(1, run_id)
        )
        handle = client.get_workflow_handle(workflow_id, run_id=temporal_run_id)
        if expected_temporal_status == WorkflowExecutionStatus.COMPLETED:
            result = await handle.result()
            assert result["outcome"] == "completed"
        else:
            with pytest.raises(WorkflowFailureError):
                await handle.result()
        recovered_run_id = await adapter.start_web_run(
            workflow_id, WebRunWorkflowInput(1, run_id)
        )
        history = await handle.fetch_history()
        description = await handle.describe()

    started = history.events[0].workflow_execution_started_event_attributes
    assert started.attempt == 1
    assert description.status == expected_temporal_status
    assert recovered_run_id == temporal_run_id
    assert integration_execute.calls == [run_id]
    assert integration_state["failed_finalized"] is True


@pytest.mark.parametrize(
    ("cancel_status", "expected_temporal_status"),
    [
        ("cancelled", WorkflowExecutionStatus.CANCELED),
        ("failed", WorkflowExecutionStatus.FAILED),
    ],
)
async def test_td_020_td_021_real_temporal_cancel_uses_domain_authority(
    cancel_status: str, expected_temporal_status: WorkflowExecutionStatus
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")

    client = await Client.connect(temporal_host)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            integration_prepare,
            integration_finalize_failed,
            integration_finalize_cancelled,
        ],
        agent_activities=[integration_execute],
    )
    run_id = str(uuid4())
    workflow_id = web_workflow_id(run_id)
    reset_integration_state()
    integration_state["agent_blocks"] = True
    integration_state["cancel_status"] = cancel_status

    async with AsyncExitStack() as stack:
        await stack.enter_async_context(workers.lifecycle)
        await stack.enter_async_context(workers.agent)
        temporal_run_id = await TemporalClientAdapter(client).start_web_run(
            workflow_id, WebRunWorkflowInput(1, run_id)
        )
        handle = client.get_workflow_handle(workflow_id, run_id=temporal_run_id)
        await asyncio.wait_for(integration_state["agent_started"].wait(), timeout=10)
        await handle.cancel()
        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(handle.result(), timeout=20)
        description = await handle.describe()

    assert integration_state["agent_cancelled"] is True
    assert integration_state["cancel_finalized"] is True
    assert description.status == expected_temporal_status
