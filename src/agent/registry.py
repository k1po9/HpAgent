"""InMemoryAgentRegistry —— 基于能力的代理路由。

find_best 返回最合适的单个代理（而非列表）。
匹配依据：能力标签交集、优先级和成本等级。
"""

from __future__ import annotations

from .interfaces import AgentRegistry, BaseAgent
from .types import CapabilityRequirement


class InMemoryAgentRegistry(AgentRegistry):
    """Process-local agent registry.
    进程内 Agent 注册表。
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

        async def find_best(self, requirement: CapabilityRequirement) -> BaseAgent | None:
            """按能力交集选择最匹配的代理。

            评分规则：
                1. required_tags 与 agent.tags 的交集大小
                2. 更高的 priority 获胜
                3. 更低的 cost_tier 获胜（default < standard < premium）
            """
            candidates: list[tuple[int, BaseAgent]] = []

            for agent in self._agents.values():
                agent_tags = agent.capability.tags
                if not requirement.required_tags.issubset(agent_tags):
                    continue
                if agent.capability.priority < requirement.min_priority:
                    continue

                intersection = len(requirement.required_tags & agent_tags)
                candidates.append((intersection, agent))

            if not candidates:
                return None

            # Sort by: intersection (desc), priority (desc), cost (asc)
            cost_order = {"default": 0, "standard": 1, "premium": 2}

            def sort_key(item: tuple[int, BaseAgent]) -> tuple[int, int, int]:
                score, agent = item
                return (
                    -score,
                    -agent.capability.priority,
                    cost_order.get(agent.capability.cost_tier, 99),
                )

            candidates.sort(key=sort_key)
            return candidates[0][1]

    async def get_healthy_agents(self) -> list[BaseAgent]:
        """返回所有代理（在 Phase 1 中假定均为健康）。"""
        return list(self._agents.values())

    async def get_availability(self, agent: BaseAgent) -> int:
        """返回可用的并发槽数。"""
        return agent.max_concurrency

    # ── Test helpers ──

    def register_direct(self, tag: str, agent: BaseAgent) -> None:
        """在特定标签下注册代理（用于测试）。"""
        self._agents[tag] = agent

    def unregister_direct(self, tag: str) -> None:
        """通过标签移除代理。"""
        self._agents.pop(tag, None)
