"""Generic durable tool execution and approval wait child workflow."""

from __future__ import annotations

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
    ApprovedToolExecutionInput,
    ApprovedToolExecutionResult,
    ToolExecutionInput,
    ToolExecutionResult,
)
from .lifecycle_contracts import LIFECYCLE_SCHEMA_VERSION, WaitInput
from .segments import DurableWait, execute_segment

_READ_RETRY = RetryPolicy(maximum_attempts=5)
_TOOL_RETRY = RetryPolicy(maximum_attempts=3)


@workflow.defn
class ToolExecutionWorkflow:
    def __init__(self) -> None:
        self._wait = DurableWait()
        self._approval_id: str | None = None
        self._operation_id: str | None = None

    @workflow.signal
    async def approval_decision(self, signal: ApprovalDecisionSignal) -> None:
        if (
            signal.schema_version == AGENT_SCHEMA_VERSION
            and signal.approval_id == self._approval_id
            and signal.operation_id == self._operation_id
        ):
            if self._wait.wait_id is not None:
                self._wait.notify(self._wait.wait_id)

    @workflow.run
    async def run(self, request: ToolExecutionInput) -> ToolExecutionResult:
        result = await execute_segment(
            "tool_execution_activity",
            request,
            task_queue=AGENT_TASK_QUEUE,
            result_type=ToolExecutionResult,
            start_to_close_timeout=timedelta(seconds=DURABLE_TOOL_ACTIVITY_START_TO_CLOSE_SECONDS),
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
            if result.approval_expires_at
            else workflow.now() + timedelta(hours=24)
        )

        async def probe():
            authoritative = await workflow.execute_activity(
                "file_action_approval_status_activity",
                ApprovalStatusInput(
                    AGENT_SCHEMA_VERSION,
                    request.account_id,
                    request.run_id,
                    request.operation_id,
                    result.approval_id,
                ),
                task_queue=AGENT_TASK_QUEUE,
                result_type=ApprovalStatusResult,
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=_READ_RETRY,
            )
            return authoritative if authoritative.status != "pending" else None

        wait = WaitInput(
            LIFECYCLE_SCHEMA_VERSION,
            request.run_id,
            request.account_id,
            f"{request.operation_id}:approval:{result.approval_id}",
            request.operation_id,
            "tool_approval",
            result.approval_id,
            expires_at.isoformat(),
        )
        try:
            authoritative = await self._wait.run(wait, probe)
        except TimeoutError:
            # A deadline is not authorization. No side effect on expiry.
            authoritative = ApprovalStatusResult(
                AGENT_SCHEMA_VERSION,
                result.approval_id,
                request.operation_id,
                "expired",
            )
        if authoritative.status == "approved":
            executed = await execute_segment(
                "approved_file_action_execution_activity",
                ApprovedToolExecutionInput(
                    AGENT_SCHEMA_VERSION,
                    request.account_id,
                    request.run_id,
                    request.operation_id,
                    0,
                    result.approval_id,
                    request.transcript_id,
                    request.transcript_version,
                    request.tool_call.tool_call_id,
                    request.tool_call.name,
                ),
                task_queue=AGENT_TASK_QUEUE,
                result_type=ApprovedToolExecutionResult,
                start_to_close_timeout=timedelta(
                    seconds=DURABLE_TOOL_ACTIVITY_START_TO_CLOSE_SECONDS
                ),
                heartbeat_timeout=timedelta(seconds=45),
                retry_policy=_TOOL_RETRY,
            )
            return ToolExecutionResult(
                result.schema_version,
                result.operation_id,
                executed.result_ref,
                executed.transcript_version,
                executed.display_summary,
                result.approval_id,
                "approved",
                result.approval_expires_at,
            )
        return ToolExecutionResult(
            result.schema_version,
            result.operation_id,
            result.result_ref,
            result.transcript_version,
            result.display_summary,
            result.approval_id,
            authoritative.status,
            result.approval_expires_at,
        )
