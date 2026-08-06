"""Concrete Web Host adapters; all ownership derives from the Run in PostgreSQL."""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

from temporalio import activity

from application.context_assembly import (
    ContextAssemblyService,
    ContextIsolationError,
    WebContextBase,
)
from web_domain.lifecycle import WebRunLifecycleService

from .facade import ExecutionRequest, ExecutionResult, StableExecutionFailure


class WebExecutionContextProvider:
    """Per-Run two-phase context port; mutable state is never shared."""

    def __init__(self, service: ContextAssemblyService, base: WebContextBase):
        self._service = service
        self._base = base

    async def recall_long_term(self, recall_query: str):
        try:
            return await self._service.recall_long_term(self._base, recall_query)
        except ContextIsolationError as exc:
            raise StableExecutionFailure("memory_isolation_violation") from exc

    def compose(self, memories):
        try:
            return tuple(self._service.compose(self._base, memories))
        except ContextIsolationError as exc:
            raise StableExecutionFailure("context_build_failed") from exc


class PostgresWebRequestLoader:
    def __init__(self, database_url: object, context: ContextAssemblyService):
        self._database_url = database_url
        self._context = context

    async def load(self, run_id: str) -> ExecutionRequest:
        from persistence.uow import UnitOfWork

        def load_base():
            with UnitOfWork(self._database_url) as uow:
                row = uow.execute("SELECT account_id FROM runs WHERE run_id=%s", (UUID(run_id),)).fetchone()
            if row is None:
                raise ValueError("run not found")
            return self._context.load_base(row["account_id"], UUID(run_id))

        try:
            base = await asyncio.to_thread(load_base)
        except ContextIsolationError as exc:
            raise StableExecutionFailure("context_build_failed") from exc
        # Long-term recall remains inside the execution loop, where a rewrite
        # query exists; this loader only provides the frozen short-term snapshot.
        context = tuple(self._context.compose(base, ()))
        return ExecutionRequest(
            execution_id=run_id,
            account_id=str(base.account_id),
            conversation_id=str(base.conversation_id),
            session_id=str(base.session_id),
            user_content=base.trigger_content,
            context=context,
            trigger_message_id=str(base.trigger_message_id),
            interaction_profile=base.interaction_profile,
            context_provider=WebExecutionContextProvider(self._context, base),
        )


class LifecycleWebReplySink:
    def __init__(self, lifecycle: WebRunLifecycleService):
        self._lifecycle = lifecycle

    async def complete(self, run_id: str, result: ExecutionResult) -> None:
        await asyncio.to_thread(self._lifecycle.complete, UUID(run_id), result.content)


class TemporalActivityControl:
    """ExecutionControl backed by the current Temporal Activity cancellation token."""

    def cancelled(self) -> bool:
        try:
            return bool(activity.is_cancelled())
        except RuntimeError:
            # Direct unit tests have no Activity context; production always does.
            return False

    @property
    def deadline(self) -> datetime:
        try:
            info = activity.info()
        except RuntimeError:
            return datetime.max.replace(tzinfo=UTC)
        candidates: list[datetime] = []
        if info.start_to_close_timeout is not None:
            candidates.append(info.started_time + info.start_to_close_timeout)
        if info.schedule_to_close_timeout is not None:
            candidates.append(info.scheduled_time + info.schedule_to_close_timeout)
        return min(candidates) if candidates else datetime.max.replace(tzinfo=UTC)

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise asyncio.CancelledError

    async def heartbeat(self, phase: str) -> None:
        if phase not in {
            "starting",
            "waiting_for_workspace_lock",
            "selecting_tools",
            "generating",
            "executing_tool",
        }:
            raise ValueError("unsupported execution heartbeat phase")
        try:
            activity.heartbeat({"schema_version": 1, "phase": phase})
        except RuntimeError:
            return
