"""Web execution host: load by Run ID, execute once, then commit through ReplySink."""
from __future__ import annotations

import logging
import time
from typing import Protocol
from uuid import UUID

from common.logging import log_event
from workspace.isolation import WorkspaceRecoveryRequired

from .facade import (
    AgentExecutionFacade,
    EventSink,
    ExecutionAuditSinkFactory,
    ExecutionControl,
    ExecutionRequest,
    ExecutionResult,
    StableExecutionFailure,
)

logger = logging.getLogger("HpAgent.WebExecutionHost")


class WebRequestLoader(Protocol):
    async def load(self, run_id: str) -> ExecutionRequest: ...


class WebReplySink(Protocol):
    async def complete(self, run_id: str, result: ExecutionResult) -> None: ...


class WebEventSinkFactory(Protocol):
    def for_run(self, run_id: str) -> EventSink: ...


class WebResourcePreparation(Protocol):
    """Per-Run workspace/Sandbox preparation (``SessionResourceRecoveryService``).

    Implementations must acquire the shared Account execution lock, recover the
    Run's bound Session workspace, and create the Session Sandbox before the
    Facade selects tools, and hold the lock for the whole execution.
    """

    def lease_for_run(
        self, account_id: UUID, run_id: UUID, control: ExecutionControl | None = None
    ): ...


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
        resource_prep: WebResourcePreparation | None = None,
    ):
        self._loader = loader
        self._facade = facade
        self._events = events
        self._replies = replies
        self._control = control
        self._audit = audit
        self._resource_prep = resource_prep

    async def execute(self, run_id: str) -> ExecutionResult:
        started_at = time.monotonic()
        request = await self._loader.load(run_id)
        if request.execution_id != run_id:
            raise ValueError("Web request loader returned a different run")
        events = self._events.for_run(run_id)
        correlation = {
            "run_id": run_id,
            "execution_id": request.execution_id,
            "conversation_id": request.conversation_id or None,
            "session_id": request.session_id or None,
            "account_id": request.account_id or None,
            "surface": "web",
        }
        log_event(
            logger,
            logging.INFO,
            "agent_execution_started",
            "agent",
            status="started",
            **correlation,
        )
        try:
            # Phase E: emit the contract ``run.started`` online event when the
            # Event Sink supports it; ``assembling_context`` is the first stable
            # progress phase.  ``started`` is an optional extension of the
            # ``EventSink`` protocol, so older sinks are untouched.
            started = getattr(events, "started", None)
            if started is not None:
                await started()
            await events.progress("assembling_context", "正在启动执行。")
            audit = (
                self._audit.for_execution(request.execution_id, request)
                if self._audit is not None
                else None
            )
            if self._resource_prep is not None:
                try:
                    async with self._resource_prep.lease_for_run(
                        UUID(request.account_id), UUID(run_id), self._control
                    ):
                        result = await self._facade.execute(
                            request, self._control, events, audit
                        )
                except WorkspaceRecoveryRequired as exc:
                    # Resource preparation is not a user-cancellable state and
                    # must not be retried or misclassified as an internal error.
                    raise StableExecutionFailure("workspace_recovery_required") from exc
            else:
                result = await self._facade.execute(
                    request, self._control, events, audit
                )
            await self._replies.complete(run_id, result)
            log_event(
                logger,
                logging.INFO,
                "agent_execution_completed",
                "agent",
                status="success",
                elapsed_ms=round((time.monotonic() - started_at) * 1000),
                **correlation,
            )
            return result
        except Exception as exc:
            error_code = (
                exc.code if isinstance(exc, StableExecutionFailure) else "internal_error"
            )
            logger.exception("Web agent execution failed", extra={
                "event": "agent_execution_failed", "component": "agent",
                **correlation, "status": "failed", "error_code": error_code,
                "elapsed_ms": round((time.monotonic() - started_at) * 1000),
            })
            raise
        finally:
            close = getattr(events, "close", None)
            if close is not None:
                await close()
