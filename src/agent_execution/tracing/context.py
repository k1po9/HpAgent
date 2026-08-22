from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator


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


@contextmanager
def trace_context(context: TraceContext) -> Iterator[TraceContext]:
    token = _current_trace.set(context)
    try:
        yield context
    finally:
        _current_trace.reset(token)
