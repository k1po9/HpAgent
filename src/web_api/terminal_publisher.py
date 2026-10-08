"""Terminal Event Publisher: committed terminal facts only.

Claims ``publish_terminal_event`` Outbox rows (consumer ``terminal-publisher``),
publishes the full committed RunSnapshot as ``run.succeeded`` / ``run.failed`` /
``run.cancelled`` to the raw Redis channel ``hpagent:web:run:{run_id}``, then
marks the Outbox row processed.

Guarantees (hpagent-web-api-contract.md §12.4, §12.5):
- Publishing failures never change the database terminal state; the row is not
  marked processed, so an at-least-once retry re-publishes.
- The stable ``terminal_event_id`` from the Outbox payload is reused on retries,
  so duplicate delivery is harmless.
- Terminal events carry ``stream_id/event_seq = null`` (they are not online
  deltas).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from common.logging import log_event
from web_domain.outbox import OutboxService

from .config import WebApiSettings
from .sse import AsyncRedis, envelope, load_run_snapshot

_TOPIC_PREFIX = "hpagent:web:run:"
logger = logging.getLogger("HpAgent.TerminalPublisher")

_TERMINAL_EVENT = {
    "succeeded": "run.succeeded",
    "failed": "run.failed",
    "cancelled": "run.cancelled",
}


class TerminalEventPublisher:
    """Best-effort Outbox-driven terminal event publisher (never a DB author)."""

    def __init__(
        self,
        database: object,
        redis_client: AsyncRedis,
        settings: WebApiSettings,
    ):
        self.database = database
        self.redis = redis_client
        self.settings = settings
        self.outbox = OutboxService(database)
        self.worker_id = "terminal-publisher"
        self._task: asyncio.Task[None] | None = None
        self.published = 0

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def _run(self) -> None:
        while True:
            if self.redis is None:
                return
            await asyncio.to_thread(self.outbox.recover_expired,120,{'publish_terminal_event','publish_work_event'})
            events = await asyncio.to_thread(
                self.outbox.claim, self.worker_id, {"publish_terminal_event", "publish_work_event"}, 10
            )
            if not events:
                await asyncio.sleep(self.settings.terminal_publisher_poll_seconds)
                continue
            for event in events:
                await self._publish_one(event)

    async def _publish_one(self, event: dict) -> None:
        event_id = UUID(str(event["outbox_event_id"]))
        account_id = UUID(str(event["account_id"]))
        if event['event_type'] == 'publish_work_event':
            await self._publish_work(event)
            return
        if event['work_id'] is not None or event['conversation_id'] is None:
            # Consume legacy Work terminal rows without creating Chat projections.
            # Current Work producers publish their own publish_work_event facts.
            await asyncio.to_thread(self.outbox.mark_processed, event_id, self.worker_id)
            return
        run_id = str(event["run_id"])
        # psycopg3 already parses jsonb columns into Python dicts; older
        # producers may hand us a JSON string, so accept both shapes.
        payload = event["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                payload = {}
        if not isinstance(payload, dict):
            payload = {}
        terminal_status = payload.get("terminal_status")
        terminal_event_id = payload.get("terminal_event_id")
        event_type = (
            _TERMINAL_EVENT.get(terminal_status)
            if isinstance(terminal_status, str)
            else None
        )
        if event_type is None or not isinstance(terminal_event_id, str):
            await asyncio.to_thread(
                self.outbox.dead_letter,
                event_id,
                self.worker_id,
                "invalid_terminal_event",
                "publish_terminal_event payload has an unknown terminal_status",
            )
            return
        try:
            snapshot = await asyncio.to_thread(
                load_run_snapshot, self.database, account_id, UUID(run_id)
            )
        except Exception:
            logger.exception("Terminal snapshot load failed", extra={
                "event": "terminal_snapshot_load_failed", "component": "sse", "run_id": run_id,
                "outbox_event_id": str(event_id), "status": "failed",
                "error_code": "terminal_snapshot_unavailable",
            })
            await asyncio.to_thread(
                self.outbox.mark_retryable_failure,
                event_id,
                self.worker_id,
                "terminal_snapshot_unavailable",
                "terminal snapshot load failed",
                datetime.now(UTC) + timedelta(seconds=1),
            )
            return
        body = envelope(
            event_type,
            event_id=terminal_event_id,
            conversation_id=snapshot["run"]["conversation_id"],
            run_id=run_id,
            message_id=snapshot.get("assistant_message", {}).get("message_id"),
            stream_id=None,
            event_seq=None,
            payload={"snapshot": snapshot},
        )
        try:
            await self.redis.publish(
                f"{_TOPIC_PREFIX}{run_id}",
                json.dumps(body, ensure_ascii=False, separators=(",", ":")),
            )
        except Exception:
            logger.exception("Terminal Redis publish failed", extra={
                "event": "redis_projection_degraded", "component": "redis", "run_id": run_id,
                "outbox_event_id": str(event_id), "status": "degraded",
                "error_code": "redis_unavailable",
            })
            await asyncio.to_thread(
                self.outbox.mark_retryable_failure,
                event_id,
                self.worker_id,
                "redis_unavailable",
                "terminal publish failed",
                datetime.now(UTC) + timedelta(seconds=1),
            )
            return
        marked = await asyncio.to_thread(
            self.outbox.mark_processed, event_id, self.worker_id
        )
        if marked:
            self.published += 1
            log_event(logger, logging.INFO, "sse_terminal_published", "sse", run_id=run_id,
                      conversation_id=snapshot["run"]["conversation_id"], status=terminal_status)

    async def _publish_work(self, event):
        from persistence.uow import UnitOfWork
        from work_domain.persistence import dto
        def load():
            with UnitOfWork(self.database) as uow:
                row = uow.execute('SELECT * FROM work_events WHERE account_id=%s AND work_id=%s AND event_id=%s', (event['account_id'],event['work_id'],event['payload']['event_id'])).fetchone()
                if row is None:
                    raise ValueError('Work event unavailable')
                return dto(row)
        try:
            row = await asyncio.to_thread(load)
            await self.redis.publish(f"hpagent:web:work:{event['work_id']}",json.dumps(row,ensure_ascii=False))
            await asyncio.to_thread(self.outbox.mark_processed,event['outbox_event_id'],self.worker_id)
        except Exception:
            await asyncio.to_thread(self.outbox.mark_retryable_failure,event['outbox_event_id'],self.worker_id,
                                   'work_projection_unavailable','Work event publish failed',datetime.now(UTC)+timedelta(seconds=2))
