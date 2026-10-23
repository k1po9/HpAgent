"""Bounded text publication; all file and database I/O lives in Activity."""

from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from .capability_contracts import CapabilityExecutionInput
from .contracts import AGENT_TASK_QUEUE
from .segments import execute_segment


@workflow.defn
class TextCapabilityWorkflow:
    @workflow.run
    async def run(self, request: CapabilityExecutionInput) -> str:
        return str(await execute_segment(
            "produce_text_activity", request, result_type=str, task_queue=AGENT_TASK_QUEUE,
            start_to_close_timeout=timedelta(seconds=60),
            retry_policy=RetryPolicy(maximum_attempts=request.max_attempts),
        ))
