from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping
from uuid import UUID


@dataclass(frozen=True)
class TraceRun:
    trace_run_id: UUID
    run_id: UUID
    account_id: UUID
    conversation_id: UUID
    strategy: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class TraceEvent:
    trace_event_id: UUID
    trace_run_id: UUID
    parent_event_id: UUID | None
    event_type: str
    name: str
    status: str
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    metadata: Mapping[str, Any]


@dataclass(frozen=True)
class TraceEventNode:
    event: TraceEvent
    children: tuple["TraceEventNode", ...] = ()


@dataclass(frozen=True)
class TraceTree:
    run: TraceRun
    roots: tuple[TraceEventNode, ...]
