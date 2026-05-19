"""LLMAgent —— 轻量 BaseAgent，直接封装 ResourcePool。

与 ReActAgent（包装完整 HarnessRunner，含会话/记忆/渠道）不同，
LLMAgent 无状态：通过 task.input_data 接收上下文，仅委托给 ResourcePool.generate()。
"""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)

# 工具执行器: async (name, arguments) -> {"output": ..., "error": ...}
ToolExecutor = Any


class LLMAgent(BaseAgent):
    """轻量 Agent，直调 ResourcePool，多agent系统中的基本单位。"""

    def __init__(
        self,
        resource_pool: Any,  # ResourcePool
        model_selector: str = "default",
        system_prompt: str = "",
        capability_spec: CapabilitySpec | None = None,
        tools: list[dict] | None = None,
        tool_executor: ToolExecutor | None = None,
        max_tool_turns: int = 5,
    ) -> None:
        self._pool = resource_pool
        self._model_selector = model_selector
        self._system_prompt = system_prompt
        self._capability = capability_spec or CapabilitySpec(
            tags={"default"},
            priority=0,
            cost_tier="default",
        )
        self._tools = tools
        self._tool_executor = tool_executor
        self._max_tool_turns = max_tool_turns

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        start = time.monotonic()

        try:
            messages = self._build_messages(task)
            final_content = await self._run_loop(messages, task)

            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                output=final_content,
                metrics=ExecutionMetrics(
                    duration_ms=(time.monotonic() - start) * 1000,
                ),
                trace_id=context.trace_id,
            )
        except Exception as exc:
            logger.exception("LLMAgent 执行失败 task=%s", task.task_id)
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

    def _build_messages(self, task: Task) -> list[dict]:
        """组装消息: system prompt → 上下文 → 用户目标。"""
        messages: list[dict] = []

        if self._system_prompt:
            messages.append({"role": "system", "content": self._system_prompt})

        # 注入上下文（历史对话 + 长期记忆）
        context_text = self._build_context_text(task)
        if context_text:
            messages.append({"role": "user", "content": context_text})

        messages.append({"role": "user", "content": task.goal})
        return messages

    def _build_context_text(self, task: Task) -> str:
        """从 task.input_data 提取对话历史和记忆。"""
        input_data = task.input_data or {}
        parts: list[str] = []

        memories = input_data.get("memories", "")
        if memories:
            parts.append(f"[历史相关记忆]\n{memories}")

        history = input_data.get("history")
        if history:
            parts.append(self._format_history(history))

        return "\n\n".join(parts)

    def _format_history(self, history: Any) -> str:
        """将对话历史 Event 列表格式化为文本块。"""
        if not history:
            return ""

        lines = ["[对话历史]"]
        for event in history:
            if hasattr(event, "event_type"):
                etype = str(event.event_type) if hasattr(event, "event_type") else ""
                content = ""
                if hasattr(event, "content"):
                    c = event.content
                    if isinstance(c, dict):
                        content = c.get("text") or c.get("content") or str(c)[:500]
                    else:
                        content = str(c)[:500]
                if etype and content:
                    role = (
                        "用户" if "USER" in etype.upper() else
                        "助手" if "MODEL" in etype.upper() else
                        "工具" if "TOOL" in etype.upper() else
                        etype
                    )
                    lines.append(f"[{role}]: {content}")
        return "\n".join(lines)

    async def _run_loop(self, messages: list[dict], task: Task) -> str:
        """Mini ReAct 循环 —— 模型调用 + 可选工具执行。"""
        tools = self._tools
        turns = 0

        while turns < self._max_tool_turns:
            turns += 1

            response = await self._pool.generate(
                messages=messages,
                model_selector=self._model_selector,
                tools=tools,
                stream=False,
            )

            content = response.content or ""

            # 无工具或无执行器 → 直接返回文本
            if not tools or not self._tool_executor:
                return content

            # 无工具调用 → 本轮结束
            tool_calls = getattr(response, "tool_calls", None) or []
            if not tool_calls:
                return content

            # 记录 assistant 消息 + 工具调用
            assistant_msg: dict = {"role": "assistant", "content": content}
            tc_list = []
            for tc in tool_calls:
                tc_list.append({
                    "name": tc.name if hasattr(tc, "name") else tc.get("name", ""),
                    "arguments": (
                        tc.arguments if hasattr(tc, "arguments")
                        else tc.get("arguments", {})
                    ),
                })
            assistant_msg["tool_calls"] = tc_list
            messages.append(assistant_msg)

            # 执行工具并记录结果
            for tc in tool_calls:
                name = tc.name if hasattr(tc, "name") else tc.get("name", "")
                args = (
                    tc.arguments if hasattr(tc, "arguments")
                    else tc.get("arguments", {})
                )
                tc_id = tc.id if hasattr(tc, "id") else tc.get("id", "")

                try:
                    result = await self._tool_executor(name, args)
                    output = result.get("output") if isinstance(result, dict) else str(result)
                except Exception as exc:
                    output = f"工具执行异常: {exc}"

                messages.append({
                    "role": "tool",
                    "content": str(output),
                    "tool_call_id": tc_id,
                })

        return content
