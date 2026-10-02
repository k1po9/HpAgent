"""One bounded fan-out inside a Generic Work Run; independent Agent contexts."""

import asyncio
from dataclasses import replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import is_cancelled_exception

from .contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentResult,
    ToolExecutionInput,
    ToolExecutionResult,
)
from .delegation_contracts import (
    NON_RETRYABLE_BRANCH_ERRORS,
    BranchFinishInput,
    DelegationFinishInput,
    DelegationInput,
    DelegationPrepared,
)
from .segments import execute_segment


def failure_code(error):
    cause = error
    while getattr(cause, "cause", None) is not None:
        cause = cause.cause
    return str(getattr(cause, "type", None) or type(cause).__name__)[:100]


@workflow.defn
class WorkDelegationWorkflow:
    @workflow.run
    async def run(self, tool: ToolExecutionInput) -> ToolExecutionResult:
        request = DelegationInput(
            tool,
            execution_id=tool.execution_id,
            run_id=tool.run_id,
            account_id=tool.account_id,
            operation_id=tool.operation_id,
        )
        options = dict(
            task_queue=AGENT_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        prepared = await execute_segment(
            "prepare_delegation_activity", request, result_type=DelegationPrepared, **options
        )
        if prepared.replay:
            return prepared.replay

        async def branch(child):
            status, reference, error_code = "failed", None, None
            try:
                result = await workflow.execute_child_workflow(
                    "AgentRunWorkflow",
                    child,
                    id=f"hpagent-subagent-{child.execution_id}",
                    task_queue=AGENT_TASK_QUEUE,
                    result_type=AgentResult,
                    cancellation_type=workflow.ChildWorkflowCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
                status, reference = "succeeded", result.result_ref
            except BaseException as exc:
                if isinstance(exc, asyncio.CancelledError) or is_cancelled_exception(exc):
                    raise
                if not isinstance(exc, Exception):
                    raise
                error_code = failure_code(exc)
            await workflow.execute_activity(
                "finish_branch_activity",
                BranchFinishInput(
                    AGENT_SCHEMA_VERSION,
                    child.execution_id,
                    child.run_id,
                    child.account_id,
                    status,
                    reference,
                    error_code,
                ),
                **options,
            )
            return error_code

        if not prepared.error:
            errors = await asyncio.gather(*(branch(child) for child in prepared.children))
            # One new Execution attempt, only for failed branches. Successful siblings are reused.
            if any(error and error not in NON_RETRYABLE_BRANCH_ERRORS for error in errors):
                retry = await execute_segment(
                    "prepare_delegation_activity",
                    replace(request, branch_attempt=2),
                    result_type=DelegationPrepared,
                    **options,
                )
                if not retry.error:
                    await asyncio.gather(*(branch(child) for child in retry.children))
        return await execute_segment(
            "finish_delegation_activity",
            DelegationFinishInput(
                tool,
                execution_id=tool.execution_id,
                run_id=tool.run_id,
                account_id=tool.account_id,
                operation_id=tool.operation_id,
            ),
            result_type=ToolExecutionResult,
            **options,
        )
