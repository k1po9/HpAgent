"""Legacy compatibility adapter for objects exposing ``process_turn``.

The production TurnOrchestrator has been removed. This adapter remains only
for experimental Multi-Agent tests and must not become a production route.
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
    """Wraps a legacy ``process_turn`` object as a BaseAgent implementation.

    legacy.process_turn(user_message) -> dict
    is adapted to BaseAgent.execute(task, context) -> TaskResult.
    """

    def __init__(
        self,
        harness_runner: Any = None,  # legacy test adapter
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
                # 无 legacy runner —— 返回模拟结果（仅用于实验层测试）
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    output={"result": f"Executed: {task.goal}"},
                    metrics=ExecutionMetrics(
                        duration_ms=(time.monotonic() - start) * 1000,
                    ),
                    trace_id=context.trace_id,
                )

            # 将 Task 映射为 legacy process_turn(user_message) 的消息形态
            user_msg = {
                "content": task.goal,
                "sender_id": context.session.user_id or "agent",
                "channel_type": "console",
                "session_id": context.trace_id,
                "account_id": context.session.user_id or "",
            }
            # 将 task.input_data 合并到消息中
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
