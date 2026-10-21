"""HTML orchestration contains no I/O or publisher imports."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .capability_contracts import CapabilityExecutionInput
from .contracts import AGENT_TASK_QUEUE
from .segments import execute_segment


@workflow.defn
class HtmlCapabilityWorkflow:
    @workflow.run
    async def run(self, request: CapabilityExecutionInput) -> str:
        result = await execute_segment(
            "produce_html_activity",
            request,
            result_type=str,
            task_queue=AGENT_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=300),
            retry_policy=RetryPolicy(maximum_attempts=request.max_attempts),
        )

        return str(result)
