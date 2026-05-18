"""Test InMemoryAgentRegistry and capability-based routing."""

import asyncio
import pytest
from src.agent.registry import InMemoryAgentRegistry
from src.agent.interfaces import BaseAgent
from src.agent.types import CapabilityRequirement, CapabilitySpec, Task, TaskResult


class StubAgent(BaseAgent):
    """Test agent with configurable capabilities."""
    def __init__(self, tags=None, priority=0, cost_tier="default", max_conc=1):
        self._cap = CapabilitySpec(
            tags=set(tags or []),
            priority=priority,
            cost_tier=cost_tier,
        )
        self._max_conc = max_conc

    @property
    def capability(self) -> CapabilitySpec:
        return self._cap

    async def execute(self, task, context):
        return TaskResult(task_id=task.task_id, status="completed")

    @property
    def max_concurrency(self) -> int:
        return self._max_conc


class TestInMemoryAgentRegistry:
    async def test_register_and_find(self):
        registry = InMemoryAgentRegistry()
        agent = StubAgent(tags={"code", "python"})
        registry.register_direct("code", agent)

        found = await registry.find_best(
            CapabilityRequirement(required_tags={"code"})
        )
        assert found is agent

    async def test_find_best_returns_none_for_no_match(self):
        registry = InMemoryAgentRegistry()
        agent = StubAgent(tags={"code"})
        registry.register_direct("code", agent)

        found = await registry.find_best(
            CapabilityRequirement(required_tags={"review"})
        )
        assert found is None

    async def test_find_best_prefers_higher_priority(self):
        registry = InMemoryAgentRegistry()
        low = StubAgent(tags={"code"}, priority=0)
        high = StubAgent(tags={"code"}, priority=10)
        registry.register_direct("code_low", low)
        registry.register_direct("code_high", high)

        found = await registry.find_best(
            CapabilityRequirement(required_tags={"code"}, min_priority=0)
        )
        assert found is high

    async def test_find_best_filters_by_min_priority(self):
        registry = InMemoryAgentRegistry()
        agent = StubAgent(tags={"code"}, priority=3)
        registry.register_direct("code", agent)

        found = await registry.find_best(
            CapabilityRequirement(required_tags={"code"}, min_priority=5)
        )
        assert found is None

    async def test_healthy_agents(self):
        registry = InMemoryAgentRegistry()
        a1 = StubAgent(tags={"code"})
        a2 = StubAgent(tags={"review"})
        registry.register_direct("code", a1)
        registry.register_direct("review", a2)

        healthy = await registry.get_healthy_agents()
        assert len(healthy) == 2

    async def test_availability(self):
        registry = InMemoryAgentRegistry()
        agent = StubAgent(tags={"code"}, max_conc=5)
        avail = await registry.get_availability(agent)
        assert avail == 5
