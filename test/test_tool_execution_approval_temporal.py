from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    ApprovalDecisionSignal,
    ApprovalStatusInput,
    ApprovalStatusResult,
    CompactToolCall,
    ToolExecutionInput,
    ToolExecutionResult,
)
from agent_workflows.tool_execution import (
    ToolExecutionWorkflow,
    tool_execution_workflow_id,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]

_state = "pending"
_status_seen: asyncio.Event
_tool_calls = 0


@activity.defn(name="tool_execution_activity")
async def approval_tool(request: ToolExecutionInput) -> ToolExecutionResult:
    global _tool_calls
    _tool_calls += 1
    if request.tool_call.name == "safe":
        return ToolExecutionResult(
            1, request.operation_id, "safe-result", request.transcript_version + 1, "safe"
        )
    return ToolExecutionResult(
        1, request.operation_id, "approval-ref", request.transcript_version,
        "approval required", "00000000-0000-0000-0000-000000000042", "pending",
        (datetime.now(UTC) + timedelta(minutes=5)).isoformat(),
    )


@activity.defn(name="file_action_approval_status_activity")
async def approval_status(request: ApprovalStatusInput) -> ApprovalStatusResult:
    _status_seen.set()
    return ApprovalStatusResult(
        1, request.approval_id, request.operation_id, _state  # type: ignore[arg-type]
    )


def _input(name: str) -> ToolExecutionInput:
    run_id = str(uuid4())
    operation_id = f"{run_id}:tool:one"
    return ToolExecutionInput(
        AGENT_SCHEMA_VERSION, run_id, str(uuid4()), str(uuid4()), str(uuid4()),
        "react", "transcript", 1, 1, operation_id, 1,
        CompactToolCall("one", name, "arguments-ref"),
    )


async def _client() -> Client:
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    return await Client.connect(host, namespace=os.getenv("TEMPORAL_NAMESPACE", "default"))


def _worker(client: Client) -> Worker:
    return Worker(
        client, task_queue=AGENT_TASK_QUEUE, workflows=[ToolExecutionWorkflow],
        activities=[approval_tool, approval_status],
    )


async def test_safe_tool_child_returns_without_waiting():
    global _tool_calls
    _tool_calls = 0
    client, request = await _client(), _input("safe")
    async with _worker(client):
        result = await client.execute_workflow(
            ToolExecutionWorkflow.run, request,
            id=tool_execution_workflow_id(request.run_id, request.operation_id),
            task_queue=AGENT_TASK_QUEUE,
        )
    assert result.approval_status == "not_required"
    assert _tool_calls == 1


@pytest.mark.parametrize("decision", ["approved", "rejected", "cancelled", "expired"])
async def test_wait_survives_worker_restart_and_uses_authoritative_decision(decision):
    global _state, _status_seen, _tool_calls
    _state, _status_seen, _tool_calls = "pending", asyncio.Event(), 0
    client, request = await _client(), _input("approval")
    workflow_id = tool_execution_workflow_id(request.run_id, request.operation_id)
    async with _worker(client):
        handle = await client.start_workflow(
            ToolExecutionWorkflow.run, request, id=workflow_id, task_queue=AGENT_TASK_QUEUE
        )
        await asyncio.wait_for(_status_seen.wait(), timeout=10)
        await handle.signal(
            ToolExecutionWorkflow.approval_decision,
            ApprovalDecisionSignal(1, str(uuid4()), request.operation_id),
        )
        await asyncio.sleep(0.1)

    _state = decision
    async with _worker(client):
        signal = ApprovalDecisionSignal(
            1, "00000000-0000-0000-0000-000000000042", request.operation_id
        )
        await handle.signal(ToolExecutionWorkflow.approval_decision, signal)
        await handle.signal(ToolExecutionWorkflow.approval_decision, signal)
        result = await asyncio.wait_for(handle.result(), timeout=10)
    assert result.approval_status == decision
    assert _tool_calls == 1
