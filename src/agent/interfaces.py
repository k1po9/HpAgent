"""Multi-agent architecture abstract interfaces.

Design decisions (from 5-round architecture review):
  - BaseAgent: atomic execution unit, wraps full ReAct loop
  - ControlStrategy: decides "what next" — swapped to change orchestration mode
  - AgentRegistry: capability-based agent routing (find_best, not [0])
  - MessageBus: pure dumb pipe — NO orchestration semantics (handoff removed)

Error handling is handled via BatchOutcome (failed_tasks_to_retry, should_terminate)
rather than a separate ErrorStrategy abstraction.
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
    """Atomic execution unit wrapping a complete ReAct loop or tool invocation."""

    @property
    @abstractmethod
    def capability(self) -> CapabilitySpec:
        """Capability declaration (tags, priority, cost tier)."""
        ...

    @abstractmethod
    async def execute(self, task: Task, context: "ExecutionContext") -> TaskResult:
        """Execute a given task and return result.

        Internally may contain the full reasoning-acting loop.
        This is the sole external entry point for an Agent.
        """
        ...

    # ── Lifecycle ──

    async def initialize(self) -> None:
        """Startup hook — resource allocation, health registration. Idempotent."""

    async def shutdown(self) -> None:
        """Shutdown hook — release resources."""

    async def health_check(self) -> AgentHealth:
        """Return current health status."""
        return AgentHealth.HEALTHY

    @property
    def max_concurrency(self) -> int:
        """Max concurrent tasks this agent can handle."""
        return 1


class ControlStrategy(ABC):
    """Orchestration control strategy: decides which tasks are ready and what to do after each batch.

    This is where Supervisor/Workflow/Council differ.
    Handoff is NOT a separate strategy — it's Agent behavior via TaskResult.handoff_request.
    """

    @abstractmethod
    async def initialize_plan(self, goal: str, context: "ExecutionContext") -> ExecutionPlan:
        """Generate initial execution plan from a top-level goal.

        Supervisor: calls LLM to decompose
        Workflow: loads pre-defined DAG
        Council: creates N identical tasks for N agents
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
        """Return a batch of ready-to-execute tasks.

        Empty list means no ready tasks (may need waiting or termination).
        """
        ...

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        context: "ExecutionContext",
    ) -> BatchOutcome:
        """Post-batch decision callback (verdict, compensation, termination, retry).

        Default: no-op.
        """
        return BatchOutcome()


class AgentRegistry(ABC):
    """Agent registry — capability-based routing with health and concurrency awareness."""

    @abstractmethod
    async def find_best(self, requirement: CapabilityRequirement) -> Optional[BaseAgent]:
        """Return the best matching agent by capability, health, concurrency slots, cost."""
        ...

    @abstractmethod
    async def get_healthy_agents(self) -> list[BaseAgent]:
        """Return all currently healthy agents."""
        ...

    @abstractmethod
    async def get_availability(self, agent: BaseAgent) -> int:
        """Return available concurrency slots for an agent."""
        ...


class MessageBus(ABC):
    """Pure communication pipe — NO orchestration semantics.

    Handoff has been removed from this layer (see TaskResult.handoff_request).
    """

    @abstractmethod
    async def send(self, target_capability: str, message: Any) -> None:
        """Send a message to agents with a specific capability."""
        ...

    @abstractmethod
    async def broadcast(self, message: Any, capabilities: list[str] | None = None) -> list[Any]:
        """Broadcast message and collect replies (for Council mode)."""
        ...

    @abstractmethod
    async def listen(self, agent_capability: str) -> AsyncIterator[Any]:
        """Subscribe to messages for a specific agent capability."""
        ...

