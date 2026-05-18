"""Verify all ABCs cannot be instantiated without concrete implementations."""

import pytest
from src.agent.interfaces import (
    AgentRegistry,
    BaseAgent,
    ControlStrategy,
    MessageBus,
)
from src.agent.types import (
    AgentHealth,
    BatchOutcome,
    CapabilitySpec,
    Task,
    TaskResult,
    TaskStatus,
)


class TestBaseAgent:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            BaseAgent()  # type: ignore

    def test_concrete_subclass_works(self):
        class SimpleAgent(BaseAgent):
            @property
            def capability(self) -> CapabilitySpec:
                return CapabilitySpec(tags={"test"})

            async def execute(self, task, context):
                return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED)

        agent = SimpleAgent()
        assert agent.capability.tags == {"test"}
        assert agent.max_concurrency == 1

    def test_lifecycle_defaults_are_noop(self):
        class SimpleAgent(BaseAgent):
            @property
            def capability(self) -> CapabilitySpec:
                return CapabilitySpec()

            async def execute(self, task, context):
                return TaskResult()

        agent = SimpleAgent()
        # These should not raise
        import asyncio
        asyncio.run(agent.initialize())
        asyncio.run(agent.shutdown())
        health = asyncio.run(agent.health_check())
        assert health == AgentHealth.HEALTHY


class TestControlStrategy:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            ControlStrategy()  # type: ignore

    def test_on_batch_completed_default_returns_empty(self):
        class SimpleStrategy(ControlStrategy):
            async def initialize_plan(self, goal, context):
                from src.agent.types import ExecutionPlan
                return ExecutionPlan()

            async def get_ready_batch(self, results, plan, pending, bus, context):
                return []

        import asyncio
        strategy = SimpleStrategy()
        outcome = asyncio.run(
            strategy.on_batch_completed({}, None, None)
        )
        assert isinstance(outcome, BatchOutcome)
        assert outcome.should_terminate is False


class TestAgentRegistry:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            AgentRegistry()  # type: ignore


class TestMessageBus:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            MessageBus()  # type: ignore
