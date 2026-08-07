from __future__ import annotations

import asyncio
import contextlib
from uuid import UUID

from agent_execution.web_events import RedisWebRunEventSinkFactory
from persistence.uow import UnitOfWork
from web_domain.outbox import OutboxService
from web_domain.services import CommandService

from .config import WebApiSettings


class FakeRunExecutor:
    """Non-production Outbox consumer using the real Run lifecycle service.

    When Redis is available the executor also projects the contract's *online*
    events (run.started / run.progress / message.delta) through the real
    ``RedisWebRunEventSink`` so a browser E2E can validate the live SSE pipeline.
    Terminal state always flows through the committed Outbox + Terminal Event
    Publisher — the online projection never contradicts database truth.
    """

    def __init__(
        self,
        database: object,
        settings: WebApiSettings,
        redis_client: object | None = None,
    ):
        if settings.environment == "production":
            raise ValueError("fake executor cannot run in production")
        self.outbox = OutboxService(database)
        self.lifecycle = CommandService(database)
        self.settings = settings
        self.worker_id = "phase-b-fake-executor"
        self._sinks = RedisWebRunEventSinkFactory(redis_client)
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
        # The run survived the delay window; stream the contract online events
        # (progress + a delta) so a browser can observe the live SSE projection.
        # Emitting AFTER the delay means the client's Gateway subscription is
        # already established — nothing is lost to Redis pub/sub's no-backlog.
        await self._stream_online(run_id)
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

    async def _stream_online(self, run_id: UUID) -> None:
        """Project run.started / run.progress / message.delta for one Run.

        Best-effort: without Redis (or on a publish failure) the sink degrades
        silently and the Run completes anyway.  Deltas are a volatile display
        buffer — the committed terminal snapshot is authoritative and replaces
        them client-side.
        """
        sink = self._sinks.for_run(str(run_id))
        if sink.degraded:
            return
        with UnitOfWork(self.lifecycle.database_url) as uow:
            row = uow.execute(
                "SELECT message_id FROM messages "
                "WHERE produced_by_run_id=%s AND role='assistant' "
                "ORDER BY created_at LIMIT 1",
                (run_id,),
            ).fetchone()
        if not row:
            await sink.close()
            return
        message_id = str(row["message_id"])
        content = self.settings.fake_executor_content
        try:
            await sink.started("running")
            await sink.progress("assembling_context", "正在准备测试回复")
            await asyncio.sleep(0.3)
            await sink.delta(content, message_id)
            await asyncio.sleep(0.3)
            await sink.progress("finalizing", "正在完成处理")
        finally:
            await sink.close()
