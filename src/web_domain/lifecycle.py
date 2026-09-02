"""Web-only adapter around the authoritative Run lifecycle transactions."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast
from uuid import UUID

from persistence.uow import UnitOfWork, retryable_transaction

from .errors import ResourceNotFound
from .services import CommandService

logger = logging.getLogger("HpAgent.WebRunLifecycleService")


class RunLifecycleObserver(Protocol):
    def observe_terminal(
        self,
        run_id: UUID,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...


@dataclass(frozen=True)
class LifecycleAuthority:
    run_id: str
    status: str


class WebRunLifecycleService:
    """Loads ownership from ``run_id``; callers never supply an Account ID."""

    def __init__(
        self,
        database_url: object,
        terminal_observer: RunLifecycleObserver | None = None,
    ):
        self.database_url = database_url
        self.commands = CommandService(database_url)
        self._terminal_observer = terminal_observer

    @retryable_transaction
    def prepare(
        self,
        run_id: UUID,
        workflow_id: str | None = None,
        temporal_run_id: str | None = None,
    ) -> LifecycleAuthority:
        with UnitOfWork(self.database_url) as uow:
            run = uow.execute("SELECT * FROM runs WHERE run_id=%s FOR UPDATE", (run_id,)).fetchone()
            if run is None:
                raise ResourceNotFound()
            if workflow_id is not None or temporal_run_id is not None:
                if not workflow_id or not temporal_run_id:
                    raise ValueError("both Temporal execution identifiers are required")
                execution = uow.execute(
                    "SELECT workflow_id,temporal_run_id,status FROM workflow_executions "
                    "WHERE run_id=%s AND is_current FOR UPDATE",
                    (run_id,),
                ).fetchone()
                if execution is None or str(execution["workflow_id"]) != workflow_id:
                    raise ValueError("Temporal Workflow identity does not match current execution")
                existing_run_id = execution["temporal_run_id"]
                if existing_run_id is not None and str(existing_run_id) != temporal_run_id:
                    raise ValueError("Temporal Run ID does not match current execution")
                uow.execute(
                    "UPDATE workflow_executions SET temporal_run_id=%s,status='running',"
                    "started_at=COALESCE(started_at,now()),version=version+1,updated_at=now() "
                    "WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
                    (UUID(temporal_run_id), run_id),
                )
            if run["status"] == "queued":
                uow.execute(
                    "UPDATE runs SET status='running',started_at=GREATEST(now(),created_at),"
                    "version=version+1,updated_at=now() "
                    "WHERE run_id=%s AND status='queued'", (run_id,)
                )
                return LifecycleAuthority(str(run_id), "running")
            return LifecycleAuthority(str(run_id), str(run["status"]))

    def finalize_failed(self, run_id: UUID, error_code: str, error_message: str) -> LifecycleAuthority:
        account_id = self._account_for(run_id)
        self.commands.fail_run(account_id, run_id, error_code, error_message)
        authority = cast(LifecycleAuthority, self._authority(run_id))
        self._observe_terminal(run_id, authority.status, {"error_code": error_code})
        return authority

    def complete(self, run_id: UUID, content: str) -> LifecycleAuthority:
        """The sole Web success terminal entrypoint, called by WebReplySink."""
        account_id = self._account_for(run_id)
        self.commands.complete_run(account_id, run_id, content)
        authority = cast(LifecycleAuthority, self._authority(run_id))
        self._observe_terminal(run_id, authority.status)
        return authority

    def finalize_cancelled(self, run_id: UUID) -> LifecycleAuthority:
        authority = cast(LifecycleAuthority, self._authority(run_id))
        if authority.status == "cancelling":
            account_id = self._account_for(run_id)
            self.commands.cancelled_run(account_id, run_id)
            authority = cast(LifecycleAuthority, self._authority(run_id))
            self._observe_terminal(run_id, authority.status)
            return authority
        if authority.status in ("cancelled", "completed", "failed"):
            self._observe_terminal(run_id, authority.status)
            return authority
        # A Temporal/UI cancel without database cancellation evidence is not a
        # user cancellation and must be visible as a stable domain failure.
        return self.finalize_failed(run_id, "workflow_cancelled_unexpectedly", "执行被意外取消。")

    def _observe_terminal(
        self,
        run_id: UUID,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        if self._terminal_observer is None:
            return
        try:
            self._terminal_observer.observe_terminal(run_id, status, metadata)
        except Exception:
            logger.exception(
                "Run terminal trace observation degraded",
                extra={
                    "event": "trace_terminal_observation_failed",
                    "component": "trace",
                    "run_id": str(run_id),
                    "status": "degraded",
                    "error_code": "trace_write_failed",
                },
            )

    @retryable_transaction
    def _authority(self, run_id: UUID) -> LifecycleAuthority:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            if row is None:
                raise ResourceNotFound()
            return LifecycleAuthority(str(run_id), str(row["status"]))

    @retryable_transaction
    def _account_for(self, run_id: UUID) -> UUID:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute("SELECT account_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            if row is None:
                raise ResourceNotFound()
            return UUID(str(row["account_id"]))
