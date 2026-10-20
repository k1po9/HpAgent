"""Test-only fixed Workflow with a post-commit recovery boundary."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from agent_workflows.capability_contracts import CapabilityExecutionInput
from agent_workflows.contracts import AGENT_TASK_QUEUE
from agent_workflows.segments import execute_segment


@workflow.defn
class DeterministicCapabilityWorkflow:
    @workflow.run
    async def run(self, request: CapabilityExecutionInput) -> str:
        reference = await execute_segment(
            "test_capability_publish",
            request,
            task_queue=AGENT_TASK_QUEUE,
            result_type=str,
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=RetryPolicy(maximum_attempts=request.max_attempts),
        )
        # A real durable boundary after commit, before parent delivery: kill/restart here.
        await workflow.sleep(2)
        return reference
