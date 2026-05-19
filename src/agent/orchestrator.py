"""Orchestrator —— 纯调度引擎（机械式调度）。

设计决策（来自五轮架构评审）：
    - 通过 asyncio.gather 实现批量并行执行（而非串行 while 循环）
    - 所有策略决策通过 BatchOutcome（单一决策通道）传递
    - handoff 注入为纯机械操作（不包含策略判断）
    - 补偿在主循环退出后执行（而不是在 break 后成为死代码）
    - 超时处理保留已完成的结果（使用 asyncio.Task + future.done()）
    - 不吞噬 BaseException（如 KeyboardInterrupt）
    - 补偿包含 HANDED_OFF 任务（不只是 COMPLETED）

不变量：
    - pending 中的 task_id 不应出现在 results 中（除非处于重试状态）
    - results 中的 task_id 不应出现在 pending 中（除非被标记为重试）
    - get_ready_batch 会接收到所有已执行的任务结果（包括失败）
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from .context import ExecutionContext
from .interfaces import AgentRegistry, ControlStrategy, MessageBus
from .types import (
    BatchOutcome,
    ErrorInfo,
    ExecutionPlan,
    Task,
    TaskResult,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """纯调度引擎。

    驱动执行循环：获取就绪批次 → 并行执行 → 处理结果 → 通过 BatchOutcome 收集策略决策 → 应用 → 重复。
    不包含任何策略判断逻辑。
    """

    def __init__(
        self,
        strategy: ControlStrategy,
        registry: AgentRegistry,
        bus: MessageBus,
        compensation_registry: "CompensationRegistry | None" = None,
    ) -> None:
        self.strategy = strategy
        self.registry = registry
        self.bus = bus
        self.compensation_registry = compensation_registry

    async def run(self, goal: str, context: ExecutionContext) -> dict[str, TaskResult]:
        """执行顶层目标并返回所有任务结果。"""
        plan = await self.strategy.initialize_plan(goal, context)
        results: dict[str, TaskResult] = {}
        pending: set[str] = set(plan.tasks.keys())
        terminated = False

        while pending:
            ready_batch = await self.strategy.get_ready_batch(
                results, plan, pending, self.bus, context
            )
            if not ready_batch:
                break

            # ── 带超时的并行执行 ──────────────────────────
            batch_results_map = await self._execute_batch(ready_batch, context)

            # ── 机械式结果处理（无策略判断） ─────────
            for task, result in zip(ready_batch, batch_results_map):
                results[task.task_id] = result
                pending.discard(task.task_id)

                # Handoff：纯机械注入
                if result.handoff_request:
                    if result.status == TaskStatus.FAILED:
                        raise RuntimeError(
                            f"Task {task.task_id} failed but requested handoff — "
                            f"handoff is only valid from COMPLETED tasks"
                        )
                    result.status = TaskStatus.HANDED_OFF
                    handoff_task = Task(
                        task_id=f"{task.task_id}_handoff",
                        goal=result.handoff_request.reason,
                        required_capability=result.handoff_request.target_capability,
                        input_data=result.handoff_request.context_to_pass,
                        parent_task_id=task.task_id,
                    )
                    plan.tasks[handoff_task.task_id] = handoff_task
                    pending.add(handoff_task.task_id)

            # ── 单一决策通道：strategy.on_batch_completed ──────
            outcome = await self.strategy.on_batch_completed(results, plan, context)
            results.update(outcome.injected_results)
            for new_task in outcome.new_tasks:
                plan.tasks[new_task.task_id] = new_task
                pending.add(new_task.task_id)
            pending -= outcome.tasks_to_remove
            pending |= outcome.failed_tasks_to_retry

            if outcome.should_terminate:
                terminated = True
                break

        # ── 循环退出后执行补偿（不是 break 后的死代码） ──────
        if terminated and self.compensation_registry:
            await self._execute_compensations(plan, results, context)

        return results

    # ── Private methods ────────────────────────────────────────────────────────
    async def _execute_batch(
        self, tasks: list[Task], context: ExecutionContext
    ) -> list[TaskResult]:
        """并行执行一批任务并提供超时保护。

        使用 asyncio.Task 跟踪各个 future —— 发生超时时，已完成的结果会被保留（不被覆盖）。
        """
        futures = [
            asyncio.ensure_future(self._execute_with_lifecycle(task, context))
            for task in tasks
        ]
        timeout = context.config.timeout_seconds

        try:
            await asyncio.wait_for(
                asyncio.gather(*futures, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.error("批次执行在 %ss 后超时", timeout)
            for f in futures:
                if not f.done():
                    f.cancel()

        # 收集结果 —— 保留已完成的，超时的标记为 FAILED
        results: list[TaskResult] = []
        for task, future in zip(tasks, futures):
            if future.done() and not future.cancelled():
                try:
                    raw = future.result()
                except BaseException as exc:
                    raw = exc
            else:
                raw = TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error=ErrorInfo(
                        type="Timeout",
                        message=f"Batch execution timed out after {timeout}s",
                        retryable=True,
                    ),
                )
            result = self._normalize_result(task, raw)
            results.append(result)

        return results

    async def _execute_with_lifecycle(
        self, task: Task, context: ExecutionContext
    ) -> TaskResult:
        """解析代理并执行任务。"""
        agent = await self.registry.find_best(task.required_capability)
        if not agent:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(
                    type="NoAgent",
                    message=f"No agent available for capability "
                            f"{task.required_capability.required_tags}",
                    retryable=False,
                ),
            )
        return await agent.execute(task, context)

    def _normalize_result(self, task: Task, raw: Any) -> TaskResult:
        """将原始执行结果规范化为 TaskResult。

        BaseException 子类（KeyboardInterrupt、SystemExit 等）会被重新抛出。
        普通 Exception 会被转换为带错误信息的 FAILED TaskResult。
        """
        if isinstance(raw, BaseException) and not isinstance(raw, Exception):
            raise raw
        if isinstance(raw, Exception):
            logger.exception("Task %s crashed with unexpected exception", task.task_id)
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(
                    type=type(raw).__name__,
                    message=str(raw),
                    retryable=False,
                ),
            )
        return raw

    async def _execute_compensations(
        self,
        plan: ExecutionPlan,
        results: dict[str, TaskResult],
        context: ExecutionContext,
    ) -> None:
        """对 COMPLETED 和 HANDED_OFF 任务按相反顺序执行补偿。"""
        if self.compensation_registry is None:
            return

        compensatable = [
            tid for tid, r in results.items()
            if r.status in (TaskStatus.COMPLETED, TaskStatus.HANDED_OFF)
        ]
        for tid in reversed(compensatable):
            task = plan.tasks.get(tid)
            if task and task.task_type:
                handler = self.compensation_registry.get(task.task_type)
                if handler:
                    try:
                        await handler.compensate(task, context)
                        results[tid] = TaskResult(
                            task_id=tid,
                            status=TaskStatus.COMPENSATED,
                        )
                    except Exception as exc:
                        logger.exception("Compensation failed for task %s", tid)
                        results[tid] = TaskResult(
                            task_id=tid,
                            status=TaskStatus.COMPENSATING,
                            error=ErrorInfo(
                                type="CompensationFailed",
                                message=str(exc),
                                retryable=False,
                            ),
                        )
