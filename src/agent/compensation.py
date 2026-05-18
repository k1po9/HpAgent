"""Compensation registry — instance-based, NOT global singleton.

Design decision: each Orchestrator instance owns its CompensationRegistry,
preventing task_type collisions when Orchestrators are recursively composed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

from .context import ExecutionContext
from .types import Task


class CompensationHandler(ABC):
    """Handler for rolling back side effects of a completed task."""

    @abstractmethod
    async def compensate(self, task: Task, context: ExecutionContext) -> None:
        """Execute compensation logic for the given task."""
        ...


class CompensationRegistry:
    """Per-Orchestrator-instance compensation handler registry.

    NOT a global singleton — each Orchestrator has its own instance.
    This prevents task_type collisions in recursive composition.
    """

    def __init__(self) -> None:
        self._handlers: dict[str, CompensationHandler] = {}

    def register(self, task_type: str, handler: CompensationHandler) -> None:
        """Register a compensation handler for a task type."""
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> Optional[CompensationHandler]:
        """Get the compensation handler for a task type."""
        return self._handlers.get(task_type)

