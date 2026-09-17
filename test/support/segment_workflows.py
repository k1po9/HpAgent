"""Pure Workflow scenarios for the W1-B PG/Temporal gate."""

from dataclasses import dataclass, replace
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy

from agent_workflows.contracts import AGENT_TASK_QUEUE, ModelDecisionInput
from agent_workflows.lifecycle_contracts import WaitInput
from agent_workflows.segments import DurableWait, execute_segment


@dataclass
class Scenario:
    activity: ModelDecisionInput
    reason: str = "external_callback"
    wait_seconds: float = 60


@workflow.defn
class SegmentScenarioWorkflow:
    def __init__(self):
        self.wait = DurableWait()
        self.phase = "starting"

    @workflow.signal
    async def wake(self, wait_id: str):
        self.wait.notify(wait_id)

    @workflow.query
    def state(self) -> str:
        return self.phase

    @workflow.run
    async def run(self, scenario: Scenario) -> list[int]:
        request = scenario.activity
        first = await execute_segment(
            "segment_probe_activity",
            replace(request, operation_id=f"{request.run_id}:first"),
            task_queue=AGENT_TASK_QUEUE,
            result_type=int,
            start_to_close_timeout=timedelta(seconds=5),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(maximum_attempts=3, initial_interval=timedelta(seconds=2)),
        )
        self.phase = "waiting"

        async def probe():
            ready = await workflow.execute_activity(
                "segment_ready_activity",
                request.run_id,
                result_type=bool,
                task_queue=AGENT_TASK_QUEUE,
                start_to_close_timeout=timedelta(seconds=5),
            )
            return True if ready else None

        await self.wait.run(
            WaitInput(
                1,
                request.run_id,
                request.account_id,
                f"{request.run_id}:wait",
                request.operation_id,
                scenario.reason,
                f"authority:{request.run_id}",
                (workflow.now() + timedelta(seconds=scenario.wait_seconds)).isoformat(),
            ),
            None if scenario.reason == "timer" else probe,
        )
        self.phase = "resuming"
        second = await execute_segment(
            "segment_probe_activity",
            replace(request, operation_id=f"{request.run_id}:after"),
            task_queue=AGENT_TASK_QUEUE,
            result_type=int,
            start_to_close_timeout=timedelta(seconds=5),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(maximum_attempts=3),
        )
        self.phase = "completed"
        return [first, second]
