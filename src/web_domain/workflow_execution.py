"""Short PostgreSQL transactions for Web Temporal execution facts."""

from __future__ import annotations

from uuid import UUID

from uuid6 import uuid7

from orchestration.run_lifecycle_contracts import ReconcileCandidate, StartDecision, web_workflow_id
from persistence.runs import RunRepository
from persistence.uow import UnitOfWork, retryable_transaction
from persistence.workflow_executions import WorkflowExecutionRepository
from run_domain.lifecycle import RunLifecycleService


class PostgresWorkflowExecutionStore:
    def __init__(self, database_url: object):
        self.database_url = database_url
        self.repository = WorkflowExecutionRepository()
        self.runs = RunRepository()

    @retryable_transaction
    def prepare_start(self, run_id: UUID) -> StartDecision:
        with UnitOfWork(self.database_url) as uow:
            run = self.runs.get_in_uow(uow, run_id)
            if run is None:
                return StartDecision(run_id, web_workflow_id(run_id), False)
            run = RunLifecycleService.lock(uow, run["account_id"], run_id)
            if run["source_kind"] == "work" and run["status"] in {"queued", "running"}:
                RunLifecycleService.check_work(uow, run)
            row = self.repository.lock_current(uow, run_id)
            if row:
                # A missing Temporal Run ID is an ambiguous/crashed Start.  The
                # dispatcher must retry with the same deterministic Workflow ID;
                # AlreadyStarted is then recovered by the Temporal adapter.
                return StartDecision(
                    run_id,
                    str(row["workflow_id"]),
                    row["temporal_run_id"] is None and run["status"] in ("queued", "running"),
                )
            if run["status"] != "queued":
                return StartDecision(run_id, str(run["workflow_id"]), False)
            workflow_id = str(run["workflow_id"])
            self.repository.insert_scheduled(
                uow, uuid7(), run["account_id"], run["conversation_id"], run_id, workflow_id
            )
            return StartDecision(run_id, workflow_id, True)

    @retryable_transaction
    def still_queued(self, run_id: UUID) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = self.runs.status_in_uow(uow, run_id)
            return row is not None and row["status"] == "queued"

    @retryable_transaction
    def record_started(self, run_id: UUID, temporal_run_id: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            self.repository.record_started(uow, UUID(temporal_run_id), run_id)

    @retryable_transaction
    def needs_cancel(self, run_id: UUID) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = self.runs.status_in_uow(uow, run_id)
            return row is not None and row["status"] in ("cancelling", "cancelled")

    @retryable_transaction
    def current_workflow_id(self, run_id: UUID) -> str | None:
        with UnitOfWork(self.database_url) as uow:
            row = self.repository.current_workflow(uow, run_id)
            return str(row["workflow_id"]) if row else None

    @retryable_transaction
    def record_cancel_requested(self, run_id: UUID) -> None:
        with UnitOfWork(self.database_url) as uow:
            self.repository.record_cancel_requested(uow, run_id)

    @retryable_transaction
    def reconcile_candidates(self, limit: int) -> list[ReconcileCandidate]:
        with UnitOfWork(self.database_url) as uow:
            rows = self.repository.reconcile_candidates(uow, limit)
            return [
                ReconcileCandidate(str(row["run_id"]), str(row["run_status"]), row["workflow_id"])
                for row in rows
            ]

    @retryable_transaction
    def record_temporal_fact(self, run_id: UUID, status: str) -> None:
        if status not in ("completed", "failed", "cancelled", "terminated", "timed_out"):
            raise ValueError("status is not a Temporal terminal fact")
        with UnitOfWork(self.database_url) as uow:
            self.repository.record_terminal_fact(uow, status, run_id)
