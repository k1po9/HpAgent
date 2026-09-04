from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    ApprovalStatusInput,
    ApprovalStatusResult,
    ApprovedToolExecutionInput,
    ApprovedToolExecutionResult,
    CompactToolCall,
    ToolExecutionInput,
    ToolExecutionResult,
)
from agent_workflows.tool_execution import ToolExecutionWorkflow, tool_execution_workflow_id
from file_domain.approvals import FileActionApprovalService
from orchestration.web_dispatcher import (
    TemporalClientAdapter,
    TemporalOutboxDispatcher,
    WebOutboxDispatcher,
)
from web_domain.outbox import OutboxService
from web_domain.services import CommandService

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.temporal]

_approval_id = ""
_expires_at = ""
_approval_store: FileActionApprovalService
_waiting: asyncio.Event


@activity.defn(name="tool_execution_activity")
async def pending_tool(request: ToolExecutionInput) -> ToolExecutionResult:
    return ToolExecutionResult(
        1, request.operation_id, "approval-ref", request.transcript_version,
        "approval required", _approval_id, "pending", _expires_at,
    )


@activity.defn(name="file_action_approval_status_activity")
async def postgres_approval_status(request: ApprovalStatusInput) -> ApprovalStatusResult:
    approval = await asyncio.to_thread(
        _approval_store.authoritative_status,
        UUID(request.account_id), UUID(request.run_id), request.operation_id,
        UUID(request.approval_id),
    )
    _waiting.set()
    return ApprovalStatusResult(
        1, str(approval.approval_id), approval.operation_id, approval.status
    )


@activity.defn(name="approved_file_action_execution_activity")
async def approved_execution(request: ApprovedToolExecutionInput) -> ApprovedToolExecutionResult:
    return ApprovedToolExecutionResult(1, request.operation_id, "executed", "executed", 2)


async def test_api_outbox_signal_resumes_with_postgres_authority(
    db, account_id, database_url, worker_database_url,
):
    global _approval_id, _expires_at, _approval_store, _waiting
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    api = CommandService(database_url)
    conversation_id = UUID(api.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(api.send_message(
        account_id, conversation_id, str(uuid4()), "overwrite persistent report"
    )["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    operation_id = f"{run_id}:tool:overwrite"
    _approval_store = FileActionApprovalService(worker_database_url)
    approval = _approval_store.request(
        account_id, conversation_id, run_id, operation_id,
        "save_persistent_file", "overwrite report", "e" * 64,
    )
    _approval_id = str(approval.approval_id)
    _expires_at = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    _waiting = asyncio.Event()
    db.execute(
        "UPDATE hpagent.outbox_events SET status='processed',processed_at=now() "
        "WHERE run_id=%s AND event_type='start_run'", (run_id,),
    )

    request = ToolExecutionInput(
        AGENT_SCHEMA_VERSION, str(run_id), str(account_id), str(conversation_id),
        str(uuid4()), "react", "transcript", 1, 1, operation_id, 1,
        CompactToolCall("overwrite", "save_persistent_file", "arguments-ref"),
    )
    client = await Client.connect(host, namespace=os.getenv("TEMPORAL_NAMESPACE", "default"))
    worker = Worker(
        client, task_queue=AGENT_TASK_QUEUE, workflows=[ToolExecutionWorkflow],
        activities=[pending_tool, postgres_approval_status, approved_execution],
    )
    async with worker:
        handle = await client.start_workflow(
            ToolExecutionWorkflow.run, request,
            id=tool_execution_workflow_id(str(run_id), operation_id),
            task_queue=AGENT_TASK_QUEUE,
        )
        await asyncio.wait_for(_waiting.wait(), timeout=10)
        FileActionApprovalService(database_url).decide(
            account_id, approval.approval_id, "approved", str(uuid4())
        )
        dispatcher = WebOutboxDispatcher(
            OutboxService(worker_database_url),
            TemporalOutboxDispatcher(object(), TemporalClientAdapter(client)),
            "f4-2-e2e",
        )
        assert await dispatcher.run_once() == 1
        result = await asyncio.wait_for(handle.result(), timeout=10)

    assert result.approval_status == "approved"
    assert db.execute(
        "SELECT status FROM hpagent.outbox_events "
        "WHERE event_type='file_action_approval_decided'"
    ).fetchone()[0] == "processed"
