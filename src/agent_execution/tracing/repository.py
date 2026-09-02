from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from psycopg.types.json import Jsonb
from uuid6 import uuid7

from persistence.uow import UnitOfWork, retryable_transaction

from .metadata import sanitize_trace_metadata, sanitize_trace_run_metadata
from .models import TraceEvent, TraceEventNode, TraceRun, TraceTree

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})


class PostgresTraceRepository:
    """Short, idempotent transactions for the non-authoritative trace projection."""

    def __init__(self, database: object, *, max_events: int = 1000):
        if max_events < 1 or max_events > 10_000:
            raise ValueError("trace max_events must be between 1 and 10000")
        self.database = database
        self.max_events = max_events

    @retryable_transaction
    def create_trace_run(
        self, run_id: UUID, metadata: Mapping[str, Any] | None = None
    ) -> TraceRun:
        """Create one trace per Web Run, deriving ownership from the Run itself."""
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "INSERT INTO trace_runs(trace_run_id,run_id,account_id,conversation_id,"
                "strategy,metadata) SELECT %s,r.run_id,r.account_id,r.conversation_id,"
                "CASE WHEN r.run_kind='research' THEN 'research' ELSE r.agent_strategy END,"
                "%s FROM runs r WHERE r.run_id=%s "
                "ON CONFLICT (run_id) DO NOTHING RETURNING *",
                (uuid7(), Jsonb(sanitize_trace_run_metadata(metadata)), run_id),
            ).fetchone()
            if row is None:
                row = uow.execute(
                    "SELECT * FROM trace_runs WHERE run_id=%s", (run_id,)
                ).fetchone()
            if row is None:
                raise LookupError(f"trace Run does not exist: {run_id}")
            return self._run(row)

    @retryable_transaction
    def start_event(
        self,
        run_id: UUID,
        event_id: UUID,
        parent_event_id: UUID | None,
        name: str,
        event_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> TraceEvent:
        trace_run = self.create_trace_run(run_id)
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "INSERT INTO trace_events(trace_event_id,trace_run_id,parent_event_id,"
                "name,event_type,metadata) VALUES (%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (trace_event_id) DO UPDATE SET "
                "status='running',started_at=CASE WHEN trace_events.status='running' "
                "THEN trace_events.started_at ELSE now() END,ended_at=NULL,duration_ms=NULL,"
                "metadata=trace_events.metadata || EXCLUDED.metadata RETURNING *",
                (
                    event_id,
                    trace_run.trace_run_id,
                    parent_event_id,
                    name,
                    event_type,
                    Jsonb(sanitize_trace_metadata(name, metadata)),
                ),
            ).fetchone()
            if row is None:
                row = uow.execute(
                    "SELECT * FROM trace_events WHERE trace_run_id=%s AND trace_event_id=%s",
                    (trace_run.trace_run_id, event_id),
                ).fetchone()
            if row is None:
                raise LookupError(f"trace Event does not exist: {event_id}")
            return self._event(row)

    @retryable_transaction
    def finish_event(
        self,
        run_id: UUID,
        event_id: UUID,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> TraceEvent:
        if status not in _TERMINAL_STATUSES:
            raise ValueError(f"unsupported terminal trace status: {status}")
        with UnitOfWork(self.database) as uow:
            event_name = uow.execute(
                "SELECT e.name FROM trace_events e JOIN trace_runs r "
                "ON r.trace_run_id=e.trace_run_id WHERE r.run_id=%s "
                "AND e.trace_event_id=%s",
                (run_id, event_id),
            ).fetchone()
            safe_metadata = sanitize_trace_metadata(
                str(event_name["name"]) if event_name is not None else "", metadata
            )
            row = uow.execute(
                "UPDATE trace_events e SET status=%s,"
                "ended_at=COALESCE(e.ended_at,GREATEST(now(),e.started_at)),"
                "duration_ms=COALESCE(e.duration_ms,GREATEST(0,round(extract(epoch FROM "
                "(now()-e.started_at))*1000)::bigint)),metadata=e.metadata || %s "
                "FROM trace_runs r WHERE r.trace_run_id=e.trace_run_id AND r.run_id=%s "
                "AND e.trace_event_id=%s AND e.status='running' RETURNING e.*",
                (status, Jsonb(safe_metadata), run_id, event_id),
            ).fetchone()
            if row is None:
                row = uow.execute(
                    "SELECT e.* FROM trace_events e JOIN trace_runs r "
                    "ON r.trace_run_id=e.trace_run_id WHERE r.run_id=%s "
                    "AND e.trace_event_id=%s",
                    (run_id, event_id),
                ).fetchone()
            if row is None:
                raise LookupError(f"trace Event does not exist: {event_id}")
            if row["parent_event_id"] is None:
                uow.execute(
                    "UPDATE trace_events SET status=%s,"
                    "ended_at=COALESCE(ended_at,GREATEST(now(),started_at)),"
                    "duration_ms=COALESCE(duration_ms,GREATEST(0,round(extract(epoch FROM "
                    "(now()-started_at))*1000)::bigint)) WHERE trace_run_id=%s "
                    "AND trace_event_id<>%s AND status='running'",
                    (status, row["trace_run_id"], event_id),
                )
                uow.execute(
                    "UPDATE trace_runs SET status=%s,ended_at=COALESCE(ended_at,now()),"
                    "metadata=metadata || %s WHERE run_id=%s AND status='running'",
                    (status, Jsonb(sanitize_trace_run_metadata(metadata)), run_id),
                )
            return self._event(row)

    def get_trace_tree(self, account_id: UUID, run_id: UUID) -> TraceTree | None:
        """Return an ownership-scoped immutable tree for a historical Run."""
        with UnitOfWork(self.database) as uow:
            run_row = uow.execute(
                "SELECT * FROM trace_runs WHERE account_id=%s AND run_id=%s",
                (account_id, run_id),
            ).fetchone()
            if run_row is None:
                return None
            rows = uow.execute(
                "SELECT * FROM trace_events WHERE trace_run_id=%s "
                "ORDER BY started_at,trace_event_id LIMIT %s",
                (run_row["trace_run_id"], self.max_events),
            ).fetchall()
        events = [self._event(row) for row in rows]
        children: dict[UUID | None, list[TraceEvent]] = defaultdict(list)
        for event in events:
            children[event.parent_event_id].append(event)

        def node(event: TraceEvent) -> TraceEventNode:
            return TraceEventNode(event, tuple(node(item) for item in children[event.trace_event_id]))

        return TraceTree(self._run(run_row), tuple(node(item) for item in children[None]))

    @staticmethod
    def _run(row: Mapping[str, Any]) -> TraceRun:
        return TraceRun(
            trace_run_id=cast(UUID, row["trace_run_id"]),
            run_id=cast(UUID, row["run_id"]),
            account_id=cast(UUID, row["account_id"]),
            conversation_id=cast(UUID, row["conversation_id"]),
            strategy=str(row["strategy"]),
            status=str(row["status"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            metadata=cast(Mapping[str, Any], row["metadata"]),
        )

    @staticmethod
    def _event(row: Mapping[str, Any]) -> TraceEvent:
        return TraceEvent(
            trace_event_id=cast(UUID, row["trace_event_id"]),
            trace_run_id=cast(UUID, row["trace_run_id"]),
            parent_event_id=cast(UUID | None, row["parent_event_id"]),
            event_type=str(row["event_type"]),
            name=str(row["name"]),
            status=str(row["status"]),
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            duration_ms=cast(int | None, row["duration_ms"]),
            metadata=cast(Mapping[str, Any], row["metadata"]),
        )
