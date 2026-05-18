"""Test CompensationRegistry — instance-based, not global singleton."""

import asyncio
import pytest
from src.agent.compensation import CompensationHandler, CompensationRegistry
from src.agent.context import ExecutionContext
from src.agent.types import Task


class SpyHandler(CompensationHandler):
    """Records compensation calls."""
    def __init__(self):
        self.calls = []

    async def compensate(self, task, context):
        self.calls.append(task.task_id)


class TestCompensationRegistry:
    def test_instance_isolation(self):
        """Two registries should not share handlers (no global singleton)."""
        reg1 = CompensationRegistry()
        reg2 = CompensationRegistry()
        handler = SpyHandler()

        reg1.register("type_a", handler)
        assert reg1.get("type_a") is handler
        assert reg2.get("type_a") is None

    def test_get_nonexistent(self):
        reg = CompensationRegistry()
        assert reg.get("nonexistent") is None
