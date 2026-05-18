"""InMemoryAgentRegistry — capability-based agent routing.

find_best returns the best single agent (not a list).
Matches by capability tag intersection + priority + cost tier.
"""

from __future__ import annotations

from .interfaces import AgentRegistry, BaseAgent
from .types import CapabilityRequirement


class InMemoryAgentRegistry(AgentRegistry):
    """Process-local agent registry.

    For Phase 1, all agents are assumed healthy.
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}

    async def find_best(self, requirement: CapabilityRequirement) -> BaseAgent | None:
        """Find the best matching agent by capability intersection.

        Scoring:
          1. Intersection size of required_tags vs agent tags
          2. Higher priority wins
          3. Lower cost tier wins (default < standard < premium)
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
        """Return all agents (all assumed healthy in Phase 1)."""
        return list(self._agents.values())

    async def get_availability(self, agent: BaseAgent) -> int:
        """Return available concurrency slots."""
        return agent.max_concurrency

    # ── Test helpers ──

    def register_direct(self, tag: str, agent: BaseAgent) -> None:
        """Register an agent under a specific tag (for testing)."""
        self._agents[tag] = agent

    def unregister_direct(self, tag: str) -> None:
        """Remove an agent by direct tag."""
        self._agents.pop(tag, None)
