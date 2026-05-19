"""编排器工厂 —— 一行构建配置好的多Agent编排器。

提供:
  - ResourcePoolAdapter: 将 ResourcePool.generate() 适配为 CallLLM 协议
  - build_supervisor: 构建 Supervisor 模式编排器（含 RealLLMPlanner + RealLLMReviewer）
  - build_council: 构建 Council 模式编排器（并行投票 + RealLLMJudge）
  - build_workflow: 从静态 DAG 构建 Workflow 模式编排器
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from resources.resource_pool import ResourcePool

from .bus import InMemoryMessageBus
from .context import ExecutionContext
from .interfaces import BaseAgent
from .orchestrator import Orchestrator
from .registry import InMemoryAgentRegistry
from .strategies import (
    CallLLM,
    CouncilControlStrategy,
    LLMJudge,
    LLMPlanner,
    LLMReviewer,
    MajorityJudge,
    RealLLMJudge,
    RealLLMPlanner,
    RealLLMReviewer,
    SupervisorControlStrategy,
    WorkflowControlStrategy,
)
from .types import (
    CapabilityRequirement,
    CapabilitySpec,
    ExecutionPlan,
    Task,
)

logger = logging.getLogger(__name__)


class ResourcePoolAdapter:
    """将 ResourcePool.generate() 适配为 CallLLM 协议。

    ResourcePool.generate(messages, model_selector, tools, stream) → ModelResponse
    CallLLM = (messages: list[dict], tools: list[dict] | None) → Awaitable[Any]
    （返回具有 content 和 tool_calls 属性的响应对象）

    用法:
        pool = ResourcePool(credential_manager)
        await pool.initialize_models()
        call_llm = ResourcePoolAdapter(pool, model_selector="chat")
        planner = RealLLMPlanner(call_llm)
    """

    def __init__(
        self,
        resource_pool: Optional[ResourcePool],  # ResourcePool（延迟导入以避免在模块级别产生循环依赖或额外开销）
        model_selector: str = "default",
    ) -> None:
        self._pool = resource_pool
        self._model_selector = model_selector

    async def __call__(
        self, messages: list[dict], tools: list[dict] | None = None
    ) -> Any:
        return await self._pool.generate(
            messages=messages,
            model_selector=self._model_selector,
            tools=tools,
            stream=False,
        )


def build_supervisor(
    call_llm: CallLLM,
    agents: dict[str, BaseAgent] | None = None,
    planner_system_prompt: str | None = None,
    reviewer_system_prompt: str | None = None,
    max_review_rounds: int = 10,
) -> Orchestrator:
    """构建 Supervisor 模式编排器（LLM 动态规划 + 审查）。

    Args:
        call_llm: LLM 调用函数（如 ResourcePoolAdapter 实例）
        agents: {tag: agent} 要注册的 Agent 字典
        planner_system_prompt: 规划器的自定义系统提示
        reviewer_system_prompt: 审查器的自定义系统提示
        max_review_rounds: 最大规划/审查轮次（防止无限循环）

    Returns:
        配置好的 Orchestrator，可直接调用 run(goal, context)
    """
    planner = RealLLMPlanner(call_llm, system_prompt=planner_system_prompt)
    reviewer = RealLLMReviewer(
        call_llm, system_prompt=reviewer_system_prompt, max_rounds=max_review_rounds
    )

    agent_tags = set(agents.keys()) if agents else {"default"}
    strategy = SupervisorControlStrategy(planner, reviewer, agent_tags)

    registry = InMemoryAgentRegistry()
    for tag, agent in (agents or {}).items():
        registry.register_direct(tag, agent)

    bus = InMemoryMessageBus()
    return Orchestrator(strategy, registry, bus)


def build_council(
    call_llm: CallLLM,
    agents: dict[str, BaseAgent],
    council_name: str = "council",
    judge_system_prompt: str | None = None,
    use_real_judge: bool = True,
) -> Orchestrator:
    """构建 Council 模式编排器（N个Agent并行投票 + 裁决）。

    Args:
        call_llm: LLM 调用函数
        agents: {tag: agent} —— 每个成为一个投票者
        council_name: 议会任务 ID 前缀
        judge_system_prompt: 裁决器的自定义系统提示
        use_real_judge: True=RealLLMJudge, False=MajorityJudge

    Returns:
        配置好的 Orchestrator
    """
    caps = [
        CapabilityRequirement(required_tags={tag})
        for tag in agents.keys()
    ]
    judge: LLMJudge
    if use_real_judge:
        judge = RealLLMJudge(call_llm, system_prompt=judge_system_prompt)
    else:
        judge = MajorityJudge()

    strategy = CouncilControlStrategy(caps, judge, council_name=council_name)

    registry = InMemoryAgentRegistry()
    for tag, agent in agents.items():
        registry.register_direct(tag, agent)

    bus = InMemoryMessageBus()
    return Orchestrator(strategy, registry, bus)


def build_workflow(
    dag_tasks: dict[str, Task],
    dag_dependencies: dict[str, list[str]] | None = None,
    agents: dict[str, BaseAgent] | None = None,
) -> Orchestrator:
    """从静态 DAG 构建 Workflow 模式编排器。

    Args:
        dag_tasks: {task_id: Task} 定义工作流
        dag_dependencies: {task_id: [依赖task_id列表]}
        agents: {tag: agent} 要注册的 Agent

    Returns:
        配置好的 Orchestrator
    """
    strategy = WorkflowControlStrategy(
        dag_tasks=dag_tasks,
        dag_dependencies=dag_dependencies,
    )

    registry = InMemoryAgentRegistry()
    for tag, agent in (agents or {}).items():
        registry.register_direct(tag, agent)

    bus = InMemoryMessageBus()
    return Orchestrator(strategy, registry, bus)
