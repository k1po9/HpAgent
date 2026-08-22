from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Callable, Protocol
from uuid import UUID

from .context import trace_node_id


class LifecycleTraceRepository(Protocol):
    def start_event(
        self,
        run_id: UUID,
        event_id: UUID,
        parent_event_id: UUID | None,
        name: str,
        event_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> object: ...

    def finish_event(
        self,
        run_id: UUID,
        event_id: UUID,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> object: ...


class TraceLifecycleObserver:
    """Synchronous terminal fallback shared by Activities and reconcilers."""

    def __init__(
        self,
        repository: LifecycleTraceRepository,
        on_terminal: Callable[[str], None] | None = None,
    ):
        self._repository = repository
        self._on_terminal = on_terminal

    def observe_terminal(
        self,
        run_id: UUID,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        root_node_id = UUID(trace_node_id(str(run_id), "agent_execution"))
        self._repository.start_event(
            run_id,
            root_node_id,
            None,
            "AgentExecution",
            "agent",
            {"surface": "web", "terminal_fallback": True},
        )
        self._repository.finish_event(run_id, root_node_id, status, metadata)
        if self._on_terminal is not None:
            self._on_terminal(str(run_id))
