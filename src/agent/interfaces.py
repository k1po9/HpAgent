"""多代理架构的抽象接口。

设计决策（来自五轮架构评审）：
    - BaseAgent：原子执行单元，封装完整的 ReAct 循环
    - ControlStrategy：决定“下一步做什么”——通过替换策略切换编排模式
    - AgentRegistry：基于能力的代理路由（使用 find_best 而非固定索引）
    - MessageBus：纯粹的通信管道——无编排语义（已移除 handoff）

错误处理通过 BatchOutcome（failed_tasks_to_retry、should_terminate）进行，
而不是使用单独的 ErrorStrategy 抽象。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

from .types import (
    AgentHealth,
    BatchOutcome,
    CapabilityRequirement,
    CapabilitySpec,
    ExecutionPlan,
    Task,
    TaskResult,
)


class BaseAgent(ABC):
    """原子执行单元，封装完整的 ReAct 循环或工具调用。"""

    @property
    @abstractmethod
    def capability(self) -> CapabilitySpec:
        """能力声明（标签、优先级、成本等级）。"""
        ...

    @abstractmethod
    async def execute(self, task: Task, context: "ExecutionContext") -> TaskResult:
        """执行给定任务并返回结果。

        内部可能包含完整的推理-执行循环。
        这是 Agent 的唯一外部入口点。
        """
        ...

    # ── Lifecycle ──

    async def initialize(self) -> None:
        """启动钩子——资源分配、健康注册。幂等。"""

    async def shutdown(self) -> None:
        """关闭钩子——释放资源。"""

    async def health_check(self) -> AgentHealth:
        """返回当前健康状态。"""
        return AgentHealth.HEALTHY

    @property
    def max_concurrency(self) -> int:
        """该 Agent 可处理的最大并发任务数。"""
        return 1


class ControlStrategy(ABC):
    """编排控制策略：决定哪些任务已就绪以及每个批次后如何处理。

    这里是 Supervisor/Workflow/Council 区别所在。
    Handoff 不是单独的策略——它是通过 TaskResult.handoff_request 表现为 Agent 的行为。
    """

    @abstractmethod
    async def initialize_plan(self, goal: str, context: "ExecutionContext") -> ExecutionPlan:
        """从顶层目标生成初始执行计划。

        Supervisor：调用 LLM 进行分解
        Workflow：加载预定义的 DAG
        Council：为 N 个代理创建 N 个相同的任务
        """
        ...

    @abstractmethod
    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        pending: set[str],
        bus: "MessageBus",
        context: "ExecutionContext",
    ) -> list[Task]:
        """返回一批已就绪的可执行任务。

        返回空列表表示当前没有就绪任务（可能需要等待或终止）。
        """
        ...

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        context: "ExecutionContext",
    ) -> BatchOutcome:
        """批次完成后的决策回调（判决、补偿、终止、重试）。

        默认为空操作。
        """
        return BatchOutcome()


class AgentRegistry(ABC):
    """Agent 注册表——基于能力的路由，包含健康和并发感知。"""

    @abstractmethod
    async def find_best(self, requirement: CapabilityRequirement) -> Optional[BaseAgent]:
        """根据能力、健康、并发槽和成本返回最合适的代理。"""
        ...

    @abstractmethod
    async def get_healthy_agents(self) -> list[BaseAgent]:
        """返回当前所有健康的代理列表。"""
        ...

    @abstractmethod
    async def get_availability(self, agent: BaseAgent) -> int:
        """返回指定代理可用的并发槽数。"""
        ...


class MessageBus(ABC):
    """纯通信通道——不包含编排语义。

    本层已移除 handoff（详见 TaskResult.handoff_request）。
    """

    @abstractmethod
    async def send(self, target_capability: str, message: Any) -> None:
        """向具有特定能力的代理发送消息。"""
        ...

    @abstractmethod
    async def broadcast(self, message: Any, capabilities: list[str] | None = None) -> list[Any]:
        """广播消息并收集回复（用于 Council 模式）。"""
        ...

    @abstractmethod
    async def listen(self, agent_capability: str) -> AsyncIterator[Any]:
        """订阅特定代理能力的消息。"""
        ...

