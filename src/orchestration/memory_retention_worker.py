"""Consume Chat source events and durable memory actions outside Temporal."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from web_domain.errors import OutboxLeaseLost

logger = logging.getLogger("HpAgent.MemoryRetentionWorker")
MEMORY_EVENT_TYPES = frozenset({'retain_memory', 'memory_action'})


async def run_memory_retention_loop(
    outbox: Any, service: Any, worker_id: str, *, idle_seconds: float = 0.5,
) -> None:
    while True:
        try:
            events = await asyncio.to_thread(outbox.claim, worker_id, MEMORY_EVENT_TYPES, 10)
            for event in events:
                await _handle_event(outbox, service, worker_id, event)
            if not events:
                await asyncio.sleep(idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.error("Memory retention iteration failed")
            await asyncio.sleep(1)


async def _handle_event(outbox: Any, service: Any, worker_id: str, event: dict) -> None:
    try:
        await service.process_event(event, worker_id)
    except OutboxLeaseLost:
        return  # The new owner reconciles the same persisted operation.
    except Exception as exc:
        # Database failures roll back and leave this exact delivery to lease
        # recovery. Do not let a stale delivery mutate the new owner's event.
        logger.error("Memory event interrupted; awaiting lease recovery", extra={
            'outbox_event_id': str(event['outbox_event_id']),
            'error_code': 'memory_event_interrupted', 'error_type': type(exc).__name__})
