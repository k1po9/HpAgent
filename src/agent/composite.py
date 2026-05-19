"""递归组合 —— 将任意 Orchestrator 封装为 BaseAgent 的 OrchestratorAsAgent。

这是实现嵌套编排模式的关键：
    Supervisor(Workflow(TaskA, TaskB), Council(TaskC, TaskD))

设计决策（来自五轮架构评审）：
    - OrchestratorAsAgent 会检查子任务失败（并非总是返回 COMPLETED）
    - allow_partial 控制在部分失败时返回 FAILED 还是 COMPLETED
    - ResultAggregator 负责子结果的聚合
    - CompensationRegistry 为实例级别（第 1 阶段已保证递归安全）
"""

from __future__ import annotations

from .context import ExecutionContext
from .interfaces import BaseAgent
from .orchestrator import Orchestrator
from .strategies import ResultAggregator
from .types import CapabilitySpec, ErrorInfo, Task, TaskResult, TaskStatus


class OrchestratorAsAgent(BaseAgent):
    """将 Orchestrator 封装为 BaseAgent 以实现递归组合。

    任意 Orchestrator（配合任意 Strategy）都可以被封装并由父级 Orchestrator 调度，
    表现得像普通的 Agent 一样。
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
        """通过委托给内部 Orchestrator 来执行任务。

        返回：
            - COMPLETED：所有子任务成功（或 allow_partial 为 True 且部分失败）
            - FAILED：部分子任务失败且 allow_partial 为 False
        """
        # 委托给内部 orchestrator
        sub_results = await self._orchestrator.run(task.goal, context)

        # 检查子任务失败情况
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
