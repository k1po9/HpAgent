"""适配器：将现有的 HarnessRunner 封装为 BaseAgent（ReActAgent）。

该适配器将单代理的 HarnessRunner（src/harness/runner.py）桥接到多代理架构中。
HarnessRunner 的 process_turn() 对应为 BaseAgent.execute()。

在第 1 阶段测试中，HarnessRunner 是可选的——可以使用模拟（mock）代理。
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
        harness_runner: Any = None,  # HarnessRunner（延迟导入以避免循环依赖）
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
                # 无 HarnessRunner —— 返回模拟结果（用于第 1 阶段测试）
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    output={"result": f"Executed: {task.goal}"},
                    metrics=ExecutionMetrics(
                        duration_ms=(time.monotonic() - start) * 1000,
                    ),
                    trace_id=context.trace_id,
                )

            # 将 Task 映射为 HarnessRunner.process_turn(user_message) 的消息形态
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
