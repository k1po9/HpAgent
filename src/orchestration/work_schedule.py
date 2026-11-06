"""Process loop around the PostgreSQL Work scheduler."""

from __future__ import annotations

import asyncio
import logging

logger = logging.getLogger(__name__)


async def run_work_schedule_loop(service, interval_seconds=2):
    while True:
        try:
            await asyncio.to_thread(service.run_once)
        except Exception:
            logger.exception("Work scheduler iteration failed; retrying next iteration")
        await asyncio.sleep(interval_seconds)
