"""Shared fixtures and helpers for agent tests."""
import pytest

from src.agent.context import ExecutionContext, RuntimeConfig
from src.agent.types import CapabilitySpec, TaskResult, TaskStatus

pytestmark = pytest.mark.asyncio(loop_scope="function")


def make_agent(result_status=TaskStatus.COMPLETED, output=None, tags=None):
    """Create a stub agent with configurable behavior."""
    from src.agent.interfaces import BaseAgent

    class Stub(BaseAgent):
        @property
        def capability(self) -> CapabilitySpec:
            return CapabilitySpec(tags=set(tags or ["default"]))

        async def execute(self, task, context):
            return TaskResult(
                task_id=task.task_id,
                status=result_status,
                output=output or {"done": True},
            )

    return Stub()


def make_context(timeout=5):
    """Create an ExecutionContext with a short timeout for testing."""
    return ExecutionContext(config=RuntimeConfig(timeout_seconds=timeout))
