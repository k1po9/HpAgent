"""Compatibility schedule service and process loop around the PG application use cases."""

from __future__ import annotations

import asyncio
import logging

from application.work.scheduler import WorkScheduleService as WorkScheduleService

logger = logging.getLogger(__name__)


async def run_work_schedule_loop(service, interval_seconds=2):
    while True:
        try:
            await asyncio.to_thread(service.run_once)
        except Exception:
            logger.exception("Work scheduler iteration failed; retrying next iteration")
        await asyncio.sleep(interval_seconds)
