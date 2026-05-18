"""Test ReActAgent adapter."""

import asyncio
import pytest
from src.agent.adapters import ReActAgent
from src.agent.context import ExecutionContext
from src.agent.types import CapabilityRequirement, CapabilitySpec, Task, TaskStatus


class TestReActAgent:
    async def test_execute_without_harness(self):
        """ReActAgent with no HarnessRunner returns mock result."""
        agent = ReActAgent(
            harness_runner=None,
            capability_spec=CapabilitySpec(tags={"chat"}, priority=0),
        )
        task = Task(task_id="t1", goal="test goal")
        result = await agent.execute(task, ExecutionContext())
        assert result.status == TaskStatus.COMPLETED
        assert result.task_id == "t1"
        assert "test goal" in str(result.output)

    async def test_capability_property(self):
        spec = CapabilitySpec(tags={"code", "review"}, priority=5, cost_tier="premium")
        agent = ReActAgent(capability_spec=spec)
        assert agent.capability.tags == {"code", "review"}
        assert agent.capability.priority == 5
        assert agent.capability.cost_tier == "premium"

    async def test_max_concurrency_default(self):
        agent = ReActAgent()
        assert agent.max_concurrency == 1
