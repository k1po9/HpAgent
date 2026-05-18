"""Orchestrator — pure mechanical scheduling engine.

Design decisions (from 5-round architecture review):
  - Batch-parallel execution via asyncio.gather (NOT serial while loop)
  - All strategy decisions flow through BatchOutcome (single decision channel)
  - Handoff injection is pure mechanical (no policy judgment)
  - Compensation runs AFTER the while loop (not dead code after break)
  - Timeout preserves already-completed results (asyncio.Task + future.done())
  - BaseException (KeyboardInterrupt etc.) is NOT swallowed
  - Compensation includes HANDED_OFF tasks (not just COMPLETED)

Invariants:
  - pending task_ids do NOT appear in results (unless in retry state)
  - result task_ids do NOT appear in pending (unless marked for retry)
  - get_ready_batch receives all executed task results (including failed)
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
    """Pure mechanical scheduling engine.

    Drives the execution loop: fetch ready batch → parallel execute → process results →
    collect strategy decision via BatchOutcome → apply → repeat.
    Contains ZERO policy judgment logic.
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
        """Execute a top-level goal and return all task results."""
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

            # ── Parallel execution with timeout ──────────────────────────
            batch_results_map = await self._execute_batch(ready_batch, context)

            # ── Mechanical result processing (NO policy judgment) ─────────
            for task, result in zip(ready_batch, batch_results_map):
                results[task.task_id] = result
                pending.discard(task.task_id)

                # Handoff: pure mechanical injection
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

            # ── Single decision channel: strategy.on_batch_completed ──────
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

        # ── Compensation after loop exit (NOT dead code after break) ──────
        if terminated and self.compensation_registry:
            await self._execute_compensations(plan, results, context)

        return results

    # ── Private methods ────────────────────────────────────────────────────────
    async def _execute_batch(
        self, tasks: list[Task], context: ExecutionContext
    ) -> list[TaskResult]:
        """Execute a batch of tasks in parallel with timeout protection.

        Uses asyncio.Task to track individual futures — on timeout, already-completed
        results are preserved (not overwritten).
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
            logger.error("Batch execution timed out after %ss", timeout)
            for f in futures:
                if not f.done():
                    f.cancel()

        # Collect results — preserve completed, mark timed-out as FAILED
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
        """Resolve agent and execute task."""
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
        """Normalize raw execution result to TaskResult.

        BaseException subclasses (KeyboardInterrupt, SystemExit) are re-raised.
        Regular Exceptions are converted to FAILED TaskResult with error info.
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
        """Execute compensations in reverse order for COMPLETED and HANDED_OFF tasks."""
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
