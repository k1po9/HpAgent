from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, Protocol
from uuid import UUID

from agent_execution.facade import EventSink

from .context import trace_node_id
from .metadata import sanitize_trace_metadata

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
        self._active: dict[str, TraceEventSink] = {}

    def for_run(self, run_id: str) -> "TraceEventSink":
        existing = self._active.get(run_id)
        if existing is not None:
            existing.resume_persistence()
            return existing
        sink = TraceEventSink(
            run_id,
            self._downstream.for_run(run_id),
            self._repository,
            keep_open=True,
            on_terminal=self.close_run,
        )
        self._active[run_id] = sink
        return sink

    def close_run(self, run_id: str) -> None:
        sink = self._active.pop(run_id, None)
        if sink is not None:
            sink.force_close()

    def detach_run(self, run_id: str) -> None:
        """Release the cache while an in-flight terminal Activity retains its sink."""
        self._active.pop(run_id, None)


class TraceEventSink:
    """Persist trace facts and project them through the existing online sink.

    Trace storage is deliberately best-effort: an observability outage must not
    change the Agent result.  The downstream Redis sink has the same property.
    """

    def __init__(
        self,
        run_id: str,
        downstream: EventSink,
        repository: TraceWriter,
        *,
        keep_open: bool = False,
        on_terminal: Any = None,
    ):
        self._run_id_text = run_id
        self._run_id = UUID(run_id)
        self._downstream = downstream
        self._repository = repository
        self.degraded = False
        self._trace_run_ready = False
        self._keep_open = keep_open
        self._on_terminal = on_terminal
        self._closed = False
        self._node_names: dict[str, str] = {}

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
        safe_metadata = sanitize_trace_metadata(name, metadata)
        self._node_names[node_id] = name
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
                safe_metadata,
            )
        await self._downstream_call(
            "trace_start", node_id, parent_id, name, node_type, safe_metadata
        )

    async def trace_end(
        self,
        node_id: str,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        safe_metadata = sanitize_trace_metadata(
            self._node_names.get(node_id, ""), metadata
        )
        try:
            event_id = UUID(node_id)
        except ValueError:
            self._degrade_invalid_id(node_id)
            event = None
        else:
            event = await self._write(
                "finish_event", self._run_id, event_id, status, safe_metadata
            )
        duration_ms = getattr(event, "duration_ms", None)
        await self._downstream_call(
            "trace_end", node_id, status, safe_metadata, duration_ms
        )
        if node_id == trace_node_id(self._run_id_text, "agent_execution"):
            await self._terminal_close()

    async def close(self) -> None:
        if not self._keep_open:
            await self._terminal_close()

    async def _terminal_close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._downstream_call("close")
        if self._on_terminal is not None:
            self._on_terminal(self._run_id_text)

    def force_close(self) -> None:
        """Synchronous fence used by lifecycle recovery paths."""
        self._closed = True
        if hasattr(self._downstream, "closed"):
            self._downstream.closed = True

    def resume_persistence(self) -> None:
        """Let the next Activity retry a previously degraded trace database."""
        self.degraded = False

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
