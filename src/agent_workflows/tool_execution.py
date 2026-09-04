"""Generic durable tool execution and approval wait child workflow."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    DURABLE_TOOL_ACTIVITY_START_TO_CLOSE_SECONDS,
    ApprovalDecisionSignal,
    ApprovalStatusInput,
    ApprovalStatusResult,
    ToolExecutionInput,
    ToolExecutionResult,
)

_READ_RETRY = RetryPolicy(maximum_attempts=5)
_TOOL_RETRY = RetryPolicy(maximum_attempts=3)


def tool_execution_workflow_id(run_id: str, operation_id: str) -> str:
    identity = hashlib.sha256(f"{run_id}:{operation_id}".encode()).hexdigest()[:32]
    return f"hpagent-tool-{run_id}-{identity}"


@workflow.defn
class ToolExecutionWorkflow:
    def __init__(self) -> None:
        self._wake = False
        self._approval_id: str | None = None
        self._operation_id: str | None = None

    @workflow.signal
    async def approval_decision(self, signal: ApprovalDecisionSignal) -> None:
        if (signal.schema_version == AGENT_SCHEMA_VERSION
                and signal.approval_id == self._approval_id
                and signal.operation_id == self._operation_id):
            self._wake = True

    @workflow.run
    async def run(self, request: ToolExecutionInput) -> ToolExecutionResult:
        result = await workflow.execute_activity(
            "tool_execution_activity",
            request,
            task_queue=AGENT_TASK_QUEUE,
            result_type=ToolExecutionResult,
            start_to_close_timeout=timedelta(
                seconds=DURABLE_TOOL_ACTIVITY_START_TO_CLOSE_SECONDS
            ),
            heartbeat_timeout=timedelta(seconds=45),
            retry_policy=_TOOL_RETRY,
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
        if result.approval_status != "pending" or not result.approval_id:
            return result
        self._approval_id = result.approval_id
        self._operation_id = request.operation_id
        expires_at = (
            datetime.fromisoformat(result.approval_expires_at)
            if result.approval_expires_at else workflow.now() + timedelta(hours=24)
        )
        while True:
            authoritative = await workflow.execute_activity(
                "file_action_approval_status_activity",
                ApprovalStatusInput(
                    AGENT_SCHEMA_VERSION, request.account_id, request.run_id,
                    request.operation_id, result.approval_id,
                ),
                task_queue=AGENT_TASK_QUEUE,
                result_type=ApprovalStatusResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_READ_RETRY,
            )
            if authoritative.status != "pending":
                return ToolExecutionResult(
                    result.schema_version, result.operation_id, result.result_ref,
                    result.transcript_version, result.display_summary,
                    result.approval_id, authoritative.status, result.approval_expires_at,
                )
            self._wake = False
            remaining = max(0.0, (expires_at - workflow.now()).total_seconds())
            if remaining == 0:
                continue
            try:
                await workflow.wait_condition(lambda: self._wake, timeout=remaining)
            except TimeoutError:
                pass
