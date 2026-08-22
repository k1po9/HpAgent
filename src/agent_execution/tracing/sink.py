from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from agent_execution.facade import EventSink

logger = logging.getLogger("HpAgent.TraceEventSink")


class TraceWriter(Protocol):
    def create_trace_run(
        self, run_id: UUID, metadata: Mapping[str, Any] | None = None
    ) -> object: ...

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


class WebEventSinkFactory(Protocol):
    def for_run(self, run_id: str) -> EventSink: ...


class TracingWebEventSinkFactory:
    """Decorate the shared Web sink factory without changing Temporal code."""

    def __init__(self, downstream: WebEventSinkFactory, repository: TraceWriter):
        self._downstream = downstream
        self._repository = repository

    def for_run(self, run_id: str) -> "TraceEventSink":
        return TraceEventSink(run_id, self._downstream.for_run(run_id), self._repository)


class TraceEventSink:
    """Persist trace facts and project them through the existing online sink.

    Trace storage is deliberately best-effort: an observability outage must not
    change the Agent result.  The downstream Redis sink has the same property.
    """

    def __init__(self, run_id: str, downstream: EventSink, repository: TraceWriter):
        self._run_id = UUID(run_id)
        self._downstream = downstream
        self._repository = repository
        self.degraded = False
        self._trace_run_ready = False

    async def _write(self, method: str, *args: object) -> object | None:
        if self.degraded:
            return None
        try:
            function = getattr(self._repository, method)
            return await asyncio.to_thread(function, *args)
        except Exception:
            self.degraded = True
            logger.exception(
                "Trace persistence degraded",
                extra={
                    "event": "trace_persistence_degraded",
                    "component": "trace",
                    "run_id": str(self._run_id),
                    "status": "degraded",
                    "error_code": "trace_write_failed",
                },
            )
            return None

    async def _ensure_trace_run(self) -> None:
        if self._trace_run_ready or self.degraded:
            return
        created = await self._write(
            "create_trace_run", self._run_id, {"source": "web"}
        )
        self._trace_run_ready = created is not None

    async def _downstream_call(self, method: str, *args: object) -> None:
        function = getattr(self._downstream, method, None)
        if function is not None:
            await function(*args)

    async def started(self, status: str = "running") -> None:
        await self._ensure_trace_run()
        await self._downstream_call("started", status)

    async def status(self, status: str, run_version: int | None = None) -> None:
        await self._downstream_call("status", status, run_version)

    async def delta(self, delta_text: str, message_id: str) -> None:
        await self._downstream_call("delta", delta_text, message_id)

    async def progress(self, phase: str, summary: str) -> None:
        await self._ensure_trace_run()
        await self._downstream.progress(phase, summary)

    async def trace_start(
        self,
        node_id: str,
        parent_id: str | None,
        name: str,
        node_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            event_id = UUID(node_id)
            parent_event_id = UUID(parent_id) if parent_id else None
        except ValueError:
            self._degrade_invalid_id(node_id)
        else:
            await self._write(
                "start_event",
                self._run_id,
                event_id,
                parent_event_id,
                name,
                node_type,
                metadata,
            )
        await self._downstream_call(
            "trace_start", node_id, parent_id, name, node_type, metadata
        )

    async def trace_end(
        self,
        node_id: str,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        try:
            event_id = UUID(node_id)
        except ValueError:
            self._degrade_invalid_id(node_id)
            event = None
        else:
            event = await self._write(
                "finish_event", self._run_id, event_id, status, metadata
            )
        duration_ms = getattr(event, "duration_ms", None)
        await self._downstream_call(
            "trace_end", node_id, status, metadata, duration_ms
        )

    async def close(self) -> None:
        await self._downstream_call("close")

    def _degrade_invalid_id(self, node_id: str) -> None:
        self.degraded = True
        logger.error(
            "Trace persistence requires UUID node IDs",
            extra={
                "event": "trace_persistence_degraded",
                "component": "trace",
                "run_id": str(self._run_id),
                "node_id": node_id,
                "status": "degraded",
                "error_code": "invalid_trace_node_id",
            },
        )
