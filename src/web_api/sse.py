"""SSE Gateway for ``GET /api/v1/runs/{run_id}/events``.

The Gateway authenticates, verifies Run ownership, subscribes the Redis raw
channel ``hpagent:web:run:{run_id}`` (the same channel ``RedisWebRunEventSink``
publishes to — NOT the ``app:``-prefixed RedisPubSub topic), keeps a bounded
handshake buffer, sends the committed database ``run.snapshot`` first, drains
buffered online events, then streams live deltas/progress until a committed
terminal event, ``stream.degraded``, ``auth.expired`` or client disconnect.

Design rules (hpagent-web-api-contract.md §12, §13):
- Redis/Temporal objects are never exposed directly.
- Terminal state only ever comes from the committed database, re-checked after
  a terminal notification.
- Buffered online deltas are dropped when the snapshot is already terminal.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

from uuid6 import uuid7

from common.logging import log_event
from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound

from .config import WebApiSettings
from .queries import message_dto, run_dto

_TOPIC_PREFIX = "hpagent:web:run:"
logger = logging.getLogger("HpAgent.SSE")

_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
_TERMINAL_EVENT_TYPES = frozenset(
    {"run.completed", "run.failed", "run.cancelled"}
)

class AsyncRedis(Protocol):
    """The minimal async Redis surface the Gateway depends on (redis.asyncio)."""

    def pubsub(self) -> Any: ...
    def publish(self, channel: Any, message: Any, **kwargs: Any) -> Awaitable[int]: ...
    async def aclose(self) -> None: ...


def _iso_now() -> str:
    return (
        datetime.now(UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def load_run_snapshot(database: object, account_id: UUID, run_id: UUID) -> dict[str, Any]:
    """Committed Run + assistant Message snapshot, the SSE/terminal truth source."""
    with UnitOfWork(database) as uow:
        row = uow.execute(
            "SELECT r.*,m.message_id AS m_message_id,m.conversation_id AS m_conversation_id,"
            "m.role AS m_role,m.status AS m_status,m.content AS m_content,"
            "m.sequence AS m_sequence,m.client_request_id AS m_client_request_id,"
            "m.produced_by_run_id AS m_produced_by_run_id,m.created_at AS m_created_at,"
            "m.completed_at AS m_completed_at FROM runs r JOIN messages m "
            "ON m.produced_by_run_id=r.run_id WHERE r.account_id=%s AND r.run_id=%s",
            (account_id, run_id),
        ).fetchone()
        if not row:
            raise ResourceNotFound()
        message = {
            "message_id": row["m_message_id"],
            "conversation_id": row["m_conversation_id"],
            "role": row["m_role"],
            "status": row["m_status"],
            "content": row["m_content"],
            "sequence": row["m_sequence"],
            "client_request_id": row["m_client_request_id"],
            "produced_by_run_id": row["m_produced_by_run_id"],
            "created_at": row["m_created_at"],
            "completed_at": row["m_completed_at"],
        }
        return {"run": run_dto(row), "assistant_message": message_dto(message)}


def envelope(
    event_type: str,
    *,
    event_id: str,
    conversation_id: str | None,
    run_id: str,
    message_id: str | None,
    stream_id: str | None,
    event_seq: int | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "event_id": event_id,
        "event_type": event_type,
        "conversation_id": conversation_id,
        "run_id": run_id,
        "message_id": message_id,
        "stream_id": stream_id,
        "event_seq": event_seq,
        "occurred_at": _iso_now(),
        "payload": payload,
    }


def sse_frame(envelope_body: dict[str, Any]) -> str:
    data = json.dumps(envelope_body, ensure_ascii=False, separators=(",", ":"))
    return (
        f"id: {envelope_body.get('event_id', '')}\n"
        f"event: {envelope_body.get('event_type', '')}\n"
        f"data: {data}\n\n"
    )


KEEPALIVE_FRAME = ": keepalive\n\n"


class _BufferState:
    def __init__(self) -> None:
        self.degraded_reason: str | None = None
        self.bytes_used = 0


class SSEGateway:
    """One gateway for all SSE connections; per-connection state lives in a stream."""

    def __init__(
        self,
        database: object,
        settings: WebApiSettings,
        redis_client: AsyncRedis | None,
    ):
        self.database = database
        self.settings = settings
        self.redis = redis_client
        self._active = 0
        self._lock = asyncio.Lock()

    @property
    def active_connections(self) -> int:
        return self._active

    async def try_acquire(self) -> bool:
        async with self._lock:
            if self._active >= self.settings.sse_max_connections:
                return False
            self._active += 1
            return True

    async def release(self) -> None:
        async with self._lock:
            if self._active > 0:
                self._active -= 1

    async def stream(
        self,
        account_id: UUID,
        run_id: UUID,
        raw_token: str,
        auth_check: Callable[[str], bool] | None = None,
        *,
        acquired: bool = False,
    ):
        """Yield SSE frames for one Run.

        ``acquired`` marks a connection slot already held by the caller (the
        endpoint pre-checks ownership and the connection limit before the
        response starts); when False the gateway acquires its own slot.
        """
        channel = f"{_TOPIC_PREFIX}{run_id}"
        if not acquired:
            acquired = await self.try_acquire()
            if not acquired:
                yield sse_frame(self._degraded(None, run_id, "upstream_disconnected"))
                return
        try:
            try:
                snapshot = await asyncio.to_thread(
                    load_run_snapshot, self.database, account_id, run_id
                )
            except ResourceNotFound:
                yield sse_frame(self._degraded(None, run_id, "upstream_disconnected"))
                return
            conversation_id = snapshot["run"]["conversation_id"]
            log_event(logger, logging.INFO, "sse_subscribed", "sse", run_id=str(run_id),
                      conversation_id=conversation_id, status="started")
            yield sse_frame(
                envelope(
                    "run.snapshot",
                    event_id=str(uuid7()),
                    conversation_id=conversation_id,
                    run_id=str(run_id),
                    message_id=None,
                    stream_id=None,
                    event_seq=None,
                    payload={"snapshot": snapshot},
                )
            )
            if snapshot["run"]["status"] in _TERMINAL_STATUSES:
                # Snapshot is authoritative: buffered deltas (if any) are dropped.
                return
            if self.redis is None:
                yield sse_frame(
                    self._degraded(conversation_id, run_id, "redis_unavailable")
                )
                return

            try:
                pubsub = self.redis.pubsub()
                await pubsub.subscribe(channel)
            except Exception:
                yield sse_frame(
                    self._degraded(conversation_id, run_id, "redis_unavailable")
                )
                return

            queue: asyncio.Queue[str] = asyncio.Queue(
                maxsize=self.settings.sse_handshake_buffer_events
            )
            state = _BufferState()
            reader = asyncio.create_task(self._read_pubsub(pubsub, queue, state))
            seen_stream_ids: set[str] = set()
            try:
                while True:
                    if state.degraded_reason is not None:
                        yield sse_frame(
                            self._degraded(
                                conversation_id, run_id, state.degraded_reason
                            )
                        )
                        return
                    try:
                        raw = await asyncio.wait_for(
                            queue.get(),
                            timeout=self.settings.sse_keepalive_seconds,
                        )
                    except asyncio.TimeoutError:
                        if raw_token and auth_check is not None:
                            try:
                                valid = await asyncio.to_thread(
                                    auth_check, raw_token
                                )
                            except Exception:
                                valid = True
                            if not valid:
                                yield sse_frame(
                                    envelope(
                                        "auth.expired",
                                        event_id=str(uuid7()),
                                        conversation_id=conversation_id,
                                        run_id=str(run_id),
                                        message_id=None,
                                        stream_id=None,
                                        event_seq=None,
                                        payload={},
                                    )
                                )
                                return
                        yield KEEPALIVE_FRAME
                        continue
                    except asyncio.CancelledError:
                        raise
                    event = self._normalize_event(raw, conversation_id)
                    if event is None:
                        continue
                    stream_id = event.get("stream_id")
                    if stream_id is not None:
                        seen_stream_ids.add(stream_id)
                        if len(seen_stream_ids) > 1:
                            yield sse_frame(
                                self._degraded(
                                    conversation_id, run_id, "sequence_gap"
                                )
                            )
                            return
                    if event["event_type"] in _TERMINAL_EVENT_TYPES:
                        try:
                            snapshot = await asyncio.to_thread(
                                load_run_snapshot, self.database, account_id, run_id
                            )
                        except ResourceNotFound:
                            continue
                        if snapshot["run"]["status"] in _TERMINAL_STATUSES:
                            event["payload"] = {"snapshot": snapshot}
                            event["message_id"] = snapshot["assistant_message"][
                                "message_id"
                            ]
                            event["stream_id"] = None
                            event["event_seq"] = None
                            yield sse_frame(event)
                            log_event(logger, logging.INFO, "sse_terminal_snapshot_sent", "sse", run_id=str(run_id),
                                      conversation_id=conversation_id, status=snapshot["run"]["status"])
                            return
                        # Terminal claimed but not yet committed: keep streaming.
                        continue
                    yield sse_frame(event)
            finally:
                reader.cancel()
                with contextlib.suppress(Exception):
                    await asyncio.gather(reader, return_exceptions=True)
                with contextlib.suppress(Exception):
                    await pubsub.unsubscribe(channel)
                    await pubsub.aclose()
        finally:
            await self.release()
            log_event(logger, logging.INFO, "sse_disconnected", "sse", run_id=str(run_id), status="completed")

    def _degrade(self, state: _BufferState, queue: asyncio.Queue[str], reason: str) -> None:
        """Set the degradation reason and wake the stream loop if it is idle.

        The loop blocks in ``queue.get()``; if the reader degrades without
        having queued anything (e.g. the first delta alone overflows the byte
        budget), the loop would otherwise stay silent until the keepalive tick.
        An empty wakeup string makes ``queue.get()`` return and the loop's
        top-of-loop degraded check fire immediately.
        """
        state.degraded_reason = reason
        with contextlib.suppress(asyncio.QueueFull):
            queue.put_nowait("")

    async def _read_pubsub(
        self, pubsub: Any, queue: asyncio.Queue[str], state: _BufferState
    ) -> None:
        while state.degraded_reason is None:
            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=0.5
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._degrade(state, queue, "redis_unavailable")
                return
            if message is None:
                continue
            data = message.get("data")
            if data is None:
                continue
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            state.bytes_used += len(data)
            if state.bytes_used > self.settings.sse_handshake_buffer_bytes:
                self._degrade(state, queue, "handshake_buffer_overflow")
                return
            try:
                queue.put_nowait(data)
            except asyncio.QueueFull:
                self._degrade(state, queue, "handshake_buffer_overflow")
                return

    @staticmethod
    def _normalize_event(raw: str, conversation_id: str | None) -> dict[str, Any] | None:
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        event_type = data.get("event_type") or data.get("type")
        if not isinstance(event_type, str) or not event_type:
            return None
        payload = data.get("payload")
        if not isinstance(payload, dict) and isinstance(data.get("phase"), str):
            # Legacy transport payload: progress fields at the top level.
            payload = {"phase": data.get("phase"), "summary": data.get("summary")}
        if not isinstance(payload, dict):
            payload = {}
        return {
            "schema_version": data.get("schema_version", 1),
            "event_id": data.get("event_id") or str(uuid7()),
            "event_type": event_type,
            "conversation_id": conversation_id,
            "run_id": str(data.get("run_id") or ""),
            "message_id": data.get("message_id"),
            "stream_id": data.get("stream_id"),
            "event_seq": data.get("event_seq"),
            "occurred_at": data.get("occurred_at") or _iso_now(),
            "payload": payload,
        }

    @staticmethod
    def _degraded(
        conversation_id: str | None, run_id: UUID, reason: str
    ) -> dict[str, Any]:
        return envelope(
            "stream.degraded",
            event_id=str(uuid7()),
            conversation_id=conversation_id,
            run_id=str(run_id),
            message_id=None,
            stream_id=None,
            event_seq=None,
            payload={"reason": reason, "recovery": "query_run", "retry_after_ms": 2000},
        )
