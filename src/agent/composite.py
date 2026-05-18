"""Recursive composition — OrchestratorAsAgent wraps any Orchestrator as a BaseAgent.

This is the key to nested orchestration patterns:
  Supervisor(Workflow(TaskA, TaskB), Council(TaskC, TaskD))

Design decisions (from 5-round architecture review):
  - OrchestratorAsAgent checks sub-task failures (not always returning COMPLETED)
  - allow_partial controls whether partial failure returns FAILED or COMPLETED
  - ResultAggregator handles sub-result aggregation
  - CompensationRegistry is per-instance (recursive safety already ensured in Phase 1)
"""

from __future__ import annotations

from .context import ExecutionContext
from .interfaces import BaseAgent
from .orchestrator import Orchestrator
from .strategies import ResultAggregator
from .types import CapabilitySpec, ErrorInfo, Task, TaskResult, TaskStatus


class OrchestratorAsAgent(BaseAgent):
    """Wraps an Orchestrator as a BaseAgent for recursive composition.

    Any Orchestrator (with any Strategy) can be wrapped and scheduled
    by a parent Orchestrator as if it were a plain Agent.
    """

    def __init__(
        self,
        orchestrator: Orchestrator,
        capability_spec: CapabilitySpec,
        aggregator: ResultAggregator | None = None,
        allow_partial: bool = False,
    ) -> None:
        self._orchestrator = orchestrator
        self._capability = capability_spec
        self._aggregator = aggregator or ResultAggregator()
        self._allow_partial = allow_partial

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(
        self, task: Task, context: ExecutionContext
    ) -> TaskResult:
        """Execute the task by delegating to the inner Orchestrator.

        Returns:
          - COMPLETED: all sub-tasks succeeded (or allow_partial is True and some failed)
          - FAILED: some sub-tasks failed and allow_partial is False
        """
        # Delegate to inner orchestrator
        sub_results = await self._orchestrator.run(task.goal, context)

        # Check for sub-task failures
        failed = [
            tid for tid, r in sub_results.items()
            if r.status == TaskStatus.FAILED
        ]

        if failed and not self._allow_partial:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(
                    type="SubTaskFailed",
                    message=f"{len(failed)} sub-tasks failed: {failed}",
                    retryable=False,
                    partial_output=sub_results,
                ),
                trace_id=context.trace_id,
            )

        # Aggregate sub-results
        output = await self._aggregator.aggregate(sub_results, strategy="merge")
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=output,
            trace_id=context.trace_id,
        )
