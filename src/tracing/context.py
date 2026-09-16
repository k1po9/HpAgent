from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from uuid import UUID, uuid5

TRACE_NODE_NAMESPACE = UUID("c4b8af60-f08d-4a8f-bbc3-e1fc6ff6dc43")


@dataclass(frozen=True)
class TraceContext:
    """In-process parent linkage only; never place this object in Temporal History."""

    run_id: str
    parent_event_id: str | None = None


_current_trace: ContextVar[TraceContext | None] = ContextVar(
    "agent_execution_trace", default=None
)


def current_trace_context() -> TraceContext | None:
    return _current_trace.get()


def trace_node_id(run_id: str, kind: str, operation_id: str = "") -> str:
    """Derive a stable node ID without recording observability state in History."""
    return str(uuid5(TRACE_NODE_NAMESPACE, f"{run_id}:{kind}:{operation_id}"))


@contextmanager
def trace_context(context: TraceContext) -> Iterator[TraceContext]:
    token = _current_trace.set(context)
    try:
        yield context
    finally:
        _current_trace.reset(token)
