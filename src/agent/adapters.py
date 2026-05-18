"""Adapter: wraps existing HarnessRunner as a BaseAgent (ReActAgent).

This bridges the single-agent HarnessRunner (src/harness/runner.py) into
the multi-agent architecture. The HarnessRunner's process_turn() maps to
BaseAgent.execute().

For Phase 1 tests, HarnessRunner is optional — mock agents are used.
"""

from __future__ import annotations

import time
from typing import Any

from .context import ExecutionContext
from .interfaces import BaseAgent
from .types import (
    CapabilitySpec,
    ErrorInfo,
    ExecutionMetrics,
    Task,
    TaskResult,
    TaskStatus,
)


class ReActAgent(BaseAgent):
    """Wraps HarnessRunner as a BaseAgent implementation.

    HarnessRunner.process_turn(user_message) -> dict
    is adapted to BaseAgent.execute(task, context) -> TaskResult.
    """

    def __init__(
        self,
        harness_runner: Any = None,  # HarnessRunner (lazy import to avoid circular dep)
        capability_spec: CapabilitySpec | None = None,
    ) -> None:
        self._harness = harness_runner
        self._capability = capability_spec or CapabilitySpec(
            tags={"chat", "tool_use"},
            priority=0,
            cost_tier="default",
        )

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        start = time.monotonic()

        try:
            if self._harness is None:
                # No harness — return mock result (for Phase 1 testing)
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    output={"result": f"Executed: {task.goal}"},
                    metrics=ExecutionMetrics(
                        duration_ms=(time.monotonic() - start) * 1000,
                    ),
                    trace_id=context.trace_id,
                )

            # Map Task to HarnessRunner.process_turn(user_message) shape
            user_msg = {
                "content": task.goal,
                "sender_id": context.session.user_id or "agent",
                "channel_type": "console",
                "session_id": context.trace_id,
                "account_id": context.session.user_id or "",
            }
            # Merge task input_data into the message
            if task.input_data:
                user_msg.update(task.input_data)

            result = await self._harness.process_turn(user_msg)

            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                output=result,
                metrics=ExecutionMetrics(
                    duration_ms=(time.monotonic() - start) * 1000,
                ),
                trace_id=context.trace_id,
            )

        except Exception as exc:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(
                    type=type(exc).__name__,
                    message=str(exc),
                    retryable=True,
                ),
                metrics=ExecutionMetrics(
                    duration_ms=(time.monotonic() - start) * 1000,
                ),
                trace_id=context.trace_id,
            )
