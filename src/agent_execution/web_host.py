"""Web execution host: load by Run ID, execute once, then commit through ReplySink."""
from __future__ import annotations

from typing import Protocol

from .facade import (
    AgentExecutionFacade,
    EventSink,
    ExecutionAuditSinkFactory,
    ExecutionControl,
    ExecutionRequest,
    ExecutionResult,
)


class WebRequestLoader(Protocol):
    async def load(self, run_id: str) -> ExecutionRequest: ...


class WebReplySink(Protocol):
    async def complete(self, run_id: str, result: ExecutionResult) -> None: ...


class WebEventSinkFactory(Protocol):
    def for_run(self, run_id: str) -> EventSink: ...


class WebExecutionHost:
    """Owns Web persistence/output; the Facade never receives a ReplySink."""

    def __init__(
        self,
        loader: WebRequestLoader,
        facade: AgentExecutionFacade,
        events: WebEventSinkFactory,
        replies: WebReplySink,
        control: ExecutionControl,
        audit: ExecutionAuditSinkFactory | None = None,
    ):
        self._loader = loader
        self._facade = facade
        self._events = events
        self._replies = replies
        self._control = control
        self._audit = audit

    async def execute(self, run_id: str) -> ExecutionResult:
        request = await self._loader.load(run_id)
        if request.execution_id != run_id:
            raise ValueError("Web request loader returned a different run")
        events = self._events.for_run(run_id)
        try:
            await events.progress("starting", "正在启动执行。")
            audit = (
                self._audit.for_execution(request.execution_id, request)
                if self._audit is not None
                else None
            )
            result = await self._facade.execute(
                request, self._control, events, audit
            )
            await self._replies.complete(run_id, result)
            return result
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                await close()
