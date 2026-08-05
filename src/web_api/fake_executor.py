from __future__ import annotations

import asyncio
import contextlib
from uuid import UUID

from persistence.uow import UnitOfWork
from web_domain.outbox import OutboxService
from web_domain.services import CommandService

from .config import WebApiSettings


class FakeRunExecutor:
    """Non-production Outbox consumer using the real Run lifecycle service."""

    def __init__(self, database: object, settings: WebApiSettings):
        if settings.environment == "production":
            raise ValueError("fake executor cannot run in production")
        self.outbox = OutboxService(database)
        self.lifecycle = CommandService(database)
        self.settings = settings
        self.worker_id = "phase-b-fake-executor"
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _run(self) -> None:
        while True:
            events = await asyncio.to_thread(
                self.outbox.claim, self.worker_id, {"start_run", "cancel_run"}, 10
            )
            if not events:
                await asyncio.sleep(0.01)
                continue
            for event in events:
                await self._handle(event)

    async def _handle(self, event: dict) -> None:
        event_id = UUID(str(event["outbox_event_id"]))
        account_id = UUID(str(event["account_id"]))
        run_id = UUID(str(event["run_id"]))
        if event["event_type"] == "cancel_run":
            await asyncio.to_thread(self.lifecycle.cancelled_run, account_id, run_id)
            await asyncio.to_thread(
                self.outbox.mark_processed, event_id, self.worker_id
            )
            return
        started = await asyncio.to_thread(self.lifecycle.start_run, account_id, run_id)
        await asyncio.to_thread(self.outbox.mark_processed, event_id, self.worker_id)
        if not started or self.settings.fake_executor_mode == "hold":
            return
        await asyncio.sleep(self.settings.fake_executor_delay_seconds)
        with UnitOfWork(self.lifecycle.database_url) as uow:
            row = uow.execute(
                "SELECT status FROM runs WHERE account_id=%s AND run_id=%s",
                (account_id, run_id),
            ).fetchone()
        if row and row["status"] == "cancelling":
            await asyncio.to_thread(self.lifecycle.cancelled_run, account_id, run_id)
            return
        if self.settings.fake_executor_mode == "failure":
            await asyncio.to_thread(
                self.lifecycle.fail_run,
                account_id,
                run_id,
                self.settings.fake_executor_failure_code,
                "测试执行器按脚本返回失败。",
            )
        else:
            await asyncio.to_thread(
                self.lifecycle.complete_run,
                account_id,
                run_id,
                self.settings.fake_executor_content,
            )
