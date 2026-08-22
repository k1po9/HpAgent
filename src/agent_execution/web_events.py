"""Best-effort Redis event projection for one Web Run; never publishes terminal state.

The sink publishes contract-shaped online events (``run.started``,
``message.delta``, ``run.status``, ``run.progress``) to the raw Redis channel
``hpagent:web:run:{run_id}``.  Terminal state is NOT published here: it only
flows through the committed ``publish_terminal_event`` Outbox row and the
Terminal Event Publisher, so database truth can never be contradicted by a
best-effort online projection.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from uuid6 import uuid7

# Contract stable progress phases (hpagent-web-api-contract.md §12.4) plus the
# legacy phase names emitted by Phase D callers/tests.  The Gateway may normalize
# a legacy phase; an unknown phase degrades to the generic "正在处理" hint on the
# client and never becomes Message content.
PROGRESS_PHASES = frozenset(
    {
        "assembling_context",
        "recalling_memory",
        "calling_model",
        "selecting_tools",
        "executing_tool",
        "finalizing",
        "starting",
        "generating",
        "planning",
        "plan_ready",
        "executing_step",
        "evaluating_step",
        "replanning",
        "synthesizing",
        "waiting_for_account_execution",
    }
)

_RUN_STATUSES = frozenset({"queued", "running", "cancelling"})

_TOPIC_PREFIX = "hpagent:web:run:"
logger = logging.getLogger("HpAgent.RedisWebRunEventSink")


def _iso_now() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class AsyncRedisPublisher(Protocol):
    async def publish(self, topic: str, payload: str) -> object: ...


class RedisWebRunEventSinkFactory:
    def __init__(self, redis_client: AsyncRedisPublisher | None):
        self._redis = redis_client

    def for_run(self, run_id: str) -> "RedisWebRunEventSink":
        return RedisWebRunEventSink(self._redis, run_id)


class RedisWebRunEventSink:
    """One online Event Sink lifecycle for a single Run.

    ``event_seq`` is monotonic within this instance only (a ``stream_id``).
    Merging/dropping (e.g. progress rate limiting) MUST happen before a new
    event is allocated, so a drop never creates an online sequence gap.
    """

    def __init__(
        self,
        redis_client: AsyncRedisPublisher | None,
        run_id: str,
        *,
        progress_interval_seconds: float = 0.0,
    ):
        self._redis = redis_client
        self._run_id = run_id
        self._stream_id = str(uuid4())
        self._event_seq = 0
        self._progress_interval = progress_interval_seconds
        self._last_progress_at = 0.0
        self.degraded = redis_client is None
        self.closed = False

    def _envelope(
        self, event_type: str, message_id: str | None, payload: dict, *, online: bool = True
    ) -> dict:
        """Build a contract envelope.

        ``online=True`` allocates the next ``event_seq`` under this ``stream_id``
        (run.started / message.delta / run.progress).  ``online=False`` emits a
        database-state projection with null ``stream_id/event_seq``
        (e.g. ``run.status``, per hpagent-web-api-contract.md §12.4).
        """
        if online:
            self._event_seq += 1
        return {
            "schema_version": 1,
            "event_id": str(uuid7()),
            "event_type": event_type,
            "run_id": self._run_id,
            "message_id": message_id,
            "stream_id": self._stream_id if online else None,
            "event_seq": self._event_seq if online else None,
            "occurred_at": _iso_now(),
            "payload": payload,
        }

    async def _publish(self, event: dict) -> None:
        if self.closed or self._redis is None or self.degraded:
            return
        try:
            await self._redis.publish(
                f"{_TOPIC_PREFIX}{self._run_id}",
                json.dumps(event, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            # Redis is an online projection only.  The Agent execution and
            # authoritative database terminal transaction must continue.
            self.degraded = True
            logger.exception("Web Run Redis projection degraded", extra={
                "event": "redis_projection_degraded", "component": "redis",
                "run_id": self._run_id, "status": "degraded",
                "error_code": "redis_publish_failed",
            })

    async def started(self, status: str = "running") -> None:
        if status not in _RUN_STATUSES:
            raise ValueError(f"unsupported Web run status: {status}")
        await self._publish(
            self._envelope(
                "run.started",
                None,
                {"status": status, "started_at": _iso_now()},
            )
        )

    async def status(self, status: str, run_version: int | None = None) -> None:
        if status not in _RUN_STATUSES:
            raise ValueError(f"unsupported Web run status: {status}")
        payload: dict = {"status": status}
        if run_version is not None:
            payload["run_version"] = run_version
        await self._publish(
            self._envelope("run.status", None, payload, online=False)
        )

    async def delta(self, delta_text: str, message_id: str) -> None:
        if not message_id:
            raise ValueError("message.delta requires a message_id")
        await self._publish(
            self._envelope("message.delta", message_id, {"delta": delta_text})
        )

    async def progress(self, phase: str, summary: str) -> None:
        if phase not in PROGRESS_PHASES:
            raise ValueError(f"unsupported Web progress phase: {phase}")
        if self._progress_interval > 0:
            import time

            now = time.monotonic()
            if now - self._last_progress_at < self._progress_interval:
                # Dropped BEFORE allocating event_id/event_seq: no online gap.
                return
            self._last_progress_at = now
        await self._publish(
            self._envelope(
                "run.progress",
                None,
                {"phase": phase, "summary": summary[:120]},
            )
        )

    async def trace_start(
        self,
        node_id: str,
        parent_id: str | None,
        name: str,
        node_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        await self._publish(
            self._envelope(
                "trace.event",
                None,
                {
                    "action": "start",
                    "node_id": node_id,
                    "parent_id": parent_id,
                    "name": name,
                    "type": node_type,
                    "metadata": dict(metadata or {}),
                },
            )
        )

    async def trace_end(
        self,
        node_id: str,
        status: str,
        metadata: Mapping[str, Any] | None = None,
        duration_ms: int | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "action": "end",
            "node_id": node_id,
            "status": status,
            "metadata": dict(metadata or {}),
        }
        if duration_ms is not None:
            payload["duration_ms"] = duration_ms
        await self._publish(self._envelope("trace.event", None, payload))

    async def close(self) -> None:
        """Fence late model/tool callbacks from publishing after Host exit."""
        self.closed = True
