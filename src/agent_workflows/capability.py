"""Durable isolated invocation; neither the parent nor a wait holds an Activity lease."""

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception

from .capability_contracts import (
    CapabilityCall,
    CapabilityClose,
    CapabilityFinish,
    CapabilityPrepared,
)
from .contracts import AGENT_TASK_QUEUE, ToolExecutionInput, ToolExecutionResult
from .segments import execute_segment


@workflow.defn
class CapabilityInvocationWorkflow:
    @workflow.run
    async def run(self, tool: ToolExecutionInput) -> ToolExecutionResult:
        call = CapabilityCall(
            tool, tool.account_id, tool.run_id, tool.execution_id, tool.operation_id
        )
        options: dict[str, Any] = dict(
            task_queue=AGENT_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        prepared: CapabilityPrepared = await execute_segment(
            "prepare_capability_activity", call, result_type=CapabilityPrepared, **options
        )
        if prepared.replay:
            return prepared.replay
        if prepared.child and not prepared.result_committed:
            child = prepared.child
            status = "failed"
            try:
                # The registered implementation is a Workflow, not a long routing Activity.
                handle = await workflow.start_child_workflow(
                    child.workflow_name,
                    child,
                    id=f"hpagent-capability-{child.execution_id}",
                    task_queue=AGENT_TASK_QUEUE,
                    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                await asyncio.wait_for(handle, timeout=child.max_duration_seconds)
                status = "succeeded"
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError) or is_cancelled_exception(exc):
                    status = "cancelled"
                # Cancellation acknowledgement is followed by authoritative closure below.
                raise
            finally:
                await asyncio.shield(
                    workflow.execute_activity(
                        "close_capability_activity", CapabilityClose(child, status), **options
                    )
                )
        if prepared.child and prepared.result_committed:
            await workflow.execute_activity(
                "close_capability_activity", CapabilityClose(prepared.child, "succeeded"), **options
            )
        result: ToolExecutionResult = await execute_segment(
            "observe_capability_activity",
            CapabilityFinish(
                call,
                prepared.error,
                call.account_id,
                call.run_id,
                call.execution_id,
                call.operation_id,
            ),
            result_type=ToolExecutionResult,
            **options,
        )

        return result
