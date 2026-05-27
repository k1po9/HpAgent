"""MultiAgentExecutor —— 多Agent编排执行器，可注入 HarnessRunner。

本模块是单Agent HarnessRunner 与多Agent Orchestrator 之间的桥梁。
负责：加载 Agent 配置 → 构建编排器 → 运行编排 → 合成最终响应。

HarnessRunner 在 agent_mode == "multi" 时调用 execute()。
"""

from __future__ import annotations

import logging
from typing import Any

from .context import ExecutionContext, RuntimeConfig, SessionState, SharedMemory
from .factory import (
    ResourcePoolAdapter,
    build_council,
    build_supervisor,
    build_workflow,
)
from .llm_agent import LLMAgent
from .orchestrator import Orchestrator
from .types import (
    CapabilitySpec,
    Task,
    TaskResult,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# 默认 Agent 配置（单Agent兜底）
DEFAULT_AGENTS = [
    {
        "tag": "default",
        "model_selector": "chat",
        "system_prompt": "You are a helpful assistant.",
    },
]


class MultiAgentExecutor:
    """构建并运行多Agent编排器，合成最终用户响应。

    用法::

        executor = MultiAgentExecutor(
            resource_pool=pool,
            agents_config=[{"tag": "default", "model_selector": "chat", ...}],
            strategy="supervisor",
        )
        content, turns = await executor.execute(
            goal="法国的首都是什么？",
            history_events=events,
            memories_text="...",
        )
    """

    def __init__(
        self,
        resource_pool: Any,  # ResourcePool
        agents_config: list[dict] | None = None,
        strategy: str = "supervisor",
        max_review_rounds: int = 10,
        planner_system_prompt: str | None = None,
        context_builder: Any = None,  # HarnessContextBuilder (lazy)
    ) -> None:
        self._pool = resource_pool
        self._agents_config = agents_config or DEFAULT_AGENTS
        self._strategy = strategy
        self._max_review_rounds = max_review_rounds
        self._planner_system_prompt = planner_system_prompt
        self._context_builder = context_builder

    async def execute(
        self,
        goal: str,
        history_events: list | None = None,
        memories_text: str = "",
    ) -> tuple[str, int]:
        """运行多Agent编排，返回 (final_content, turns_taken)。

        返回格式与 HarnessRunner 兼容。
        """
        # 1. 构建 ExecutionContext
        context = ExecutionContext(
            session=SessionState(
                user_id="multi-agent",
                conversation_history=list(history_events or []),
            ),
            shared_memory=SharedMemory(),
            config=RuntimeConfig(
                timeout_seconds=120,
                max_retries=3,
                model_name=getattr(self._pool, "_model_selector", "chat"),
            ),
        )

        # 2. 构建编排器
        orchestrator = self._build_orchestrator()

        # 3. 运行编排
        results = await orchestrator.run(goal, context)

        # 4. 合成最终响应
        final_content = await self._synthesize(goal, results)

        turns_taken = len([r for r in results.values() if r.status == TaskStatus.COMPLETED])

        return final_content, max(turns_taken, 1)

    def _build_orchestrator(self) -> Orchestrator:
        """从配置构建编排器。"""
        # 创建 Agent 实例
        agents: dict[str, LLMAgent] = {}

        for entry in self._agents_config:
            tag = entry["tag"]
            executor = entry.get("tool_executor")
            agents[tag] = LLMAgent(
                resource_pool=self._pool,
                model_selector=entry.get("model_selector", "chat"),
                system_prompt=entry.get("system_prompt", ""),
                capability_spec=CapabilitySpec(
                    tags={tag},
                    priority=entry.get("priority", 0),
                    cost_tier=entry.get("cost_tier", "default"),
                ),
                tools=entry.get("tools"),
                tool_executor=executor,
                max_tool_turns=entry.get("max_tool_turns", 5),
            )

        # ResourcePoolAdapter: 将 ResourcePool.generate() 适配为 CallLLM 协议，供编排器调用
        call_llm = ResourcePoolAdapter(self._pool, model_selector="chat")

        # 按策略构建编排器
        if self._strategy == "council":
            return build_council(
                call_llm=call_llm,
                agents=agents,
                council_name="main",
                use_real_judge=True,
            )
        elif self._strategy == "workflow":
            logger.warning("Workflow 策略需预定义 DAG，回退到 supervisor")
            return build_supervisor(
                call_llm=call_llm,
                agents=agents,
                planner_system_prompt=self._planner_system_prompt,
                max_review_rounds=self._max_review_rounds,
            )
        else:
            # 默认: supervisor
            return build_supervisor(
                call_llm=call_llm,
                agents=agents,
                planner_system_prompt=self._planner_system_prompt,
                max_review_rounds=self._max_review_rounds,
            )

    async def _synthesize(
        self, goal: str, results: dict[str, TaskResult]
    ) -> str:
        """从所有 TaskResult 合成用户可读的最终响应。

        编排器返回多个子任务结果，本方法调用 LLM 将其合并为一条自然语言回复。
        若 LLM 调用失败则直接返回原始结果汇总。
        """
        # 收集成功结果
        parts: list[str] = []
        for tid, result in results.items():
            if result.status == TaskStatus.COMPLETED and result.output:
                output = result.output
                if isinstance(output, dict):
                    output = output.get("output") or output.get("content") or str(output)
                parts.append(f"### {tid}\n{output}")
            elif result.status == TaskStatus.FAILED and result.error:
                parts.append(f"### {tid} (失败)\n{result.error.message}")

        if not parts:
            return "未产生任何结果。"

        # 仅一个结果 → 直接返回
        if len(parts) == 1:
            return parts[0].split("\n", 1)[1] if "\n" in parts[0] else parts[0]

        # 多个结果 → LLM 合成
        summary = "\n\n".join(parts)
        synthesis_messages = [
            {
                "role": "system",
                "content": (
                    "你将多个子Agent的执行结果合成为一条连贯的回复。"
                    "综合所有子结果的关键发现，用自然语言回答用户。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题: {goal}\n\n"
                    f"子Agent结果:\n{summary}\n\n"
                    f"请向用户提供综合回答。"
                ),
            },
        ]

        try:
            response = await self._pool.generate(
                messages=synthesis_messages,
                model_selector="chat",
                stream=False,
            )
            return response.content or "合成结果为空。"
        except Exception as exc:
            logger.warning("合成失败，返回原始结果: %s", exc)
            return f"来自 {len(parts)} 个Agent的结果:\n\n{summary}"
