"""Short PostgreSQL transactions for Web Temporal execution facts."""
from __future__ import annotations

from uuid import UUID

from uuid6 import uuid7

from orchestration.web_dispatcher import StartDecision, web_workflow_id
from orchestration.web_reconciler import ReconcileCandidate
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.lifecycle import RunLifecycleService


class PostgresWorkflowExecutionStore:
    def __init__(self, database_url: object):
        self.database_url = database_url

    @retryable_transaction
    def prepare_start(self, run_id: UUID) -> StartDecision:
        with UnitOfWork(self.database_url) as uow:
            run = uow.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            if run is None:
                return StartDecision(run_id, web_workflow_id(run_id), False)
            run = RunLifecycleService.lock(uow, run['account_id'], run_id)
            if run['source_kind'] == 'work' and run['status'] in {'queued','running'}:
                RunLifecycleService.check_work(uow, run)
            row = uow.execute(
                "SELECT workflow_id,temporal_run_id,status FROM workflow_executions "
                "WHERE run_id=%s AND is_current FOR UPDATE",
                (run_id,),
            ).fetchone()
            if row:
                # A missing Temporal Run ID is an ambiguous/crashed Start.  The
                # dispatcher must retry with the same deterministic Workflow ID;
                # AlreadyStarted is then recovered by the Temporal adapter.
                return StartDecision(
                    run_id,
                    str(row["workflow_id"]),
                    row["temporal_run_id"] is None
                    and run["status"] in ("queued", "running"),
                )
            if run["status"] != "queued":
                return StartDecision(run_id, str(run["workflow_id"]), False)
            workflow_id = str(run["workflow_id"])
            uow.execute(
                "INSERT INTO workflow_executions(workflow_execution_id,account_id,conversation_id,run_id,workflow_id,execution_id) "
                "SELECT %s,%s,%s,%s,%s,execution_id FROM run_executions WHERE account_id=%s AND run_id=%s AND role='root'",
                (uuid7(), run["account_id"], run["conversation_id"], run_id, workflow_id, run["account_id"], run_id),
            )
            return StartDecision(run_id, workflow_id, True)

    @retryable_transaction
    def still_queued(self, run_id: UUID) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            return row is not None and row["status"] == "queued"

    @retryable_transaction
    def record_started(self, run_id: UUID, temporal_run_id: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            uow.execute(
                "UPDATE workflow_executions SET temporal_run_id=%s,status='running',started_at=now(),"
                "version=version+1,updated_at=now() WHERE run_id=%s AND is_current "
                "AND status IN ('scheduled','running')",
                (UUID(temporal_run_id), run_id),
            )

    @retryable_transaction
    def needs_cancel(self, run_id: UUID) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
            return row is not None and row["status"] in ("cancelling", "cancelled")

    @retryable_transaction
    def current_workflow_id(self, run_id: UUID) -> str | None:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT workflow_id FROM workflow_executions WHERE run_id=%s AND is_current", (run_id,)
            ).fetchone()
            return str(row["workflow_id"]) if row else None

    @retryable_transaction
    def record_cancel_requested(self, run_id: UUID) -> None:
        with UnitOfWork(self.database_url) as uow:
            uow.execute(
                "UPDATE workflow_executions SET status='cancel_requested',"
                "version=version+1,updated_at=now() WHERE run_id=%s AND is_current "
                "AND status IN ('scheduled','running')",
                (run_id,),
            )

    @retryable_transaction
    def reconcile_candidates(self, limit: int) -> list[ReconcileCandidate]:
        with UnitOfWork(self.database_url) as uow:
            rows = uow.execute(
                "SELECT r.run_id,r.status AS run_status,w.workflow_id FROM runs r "
                "LEFT JOIN workflow_executions w ON w.run_id=r.run_id AND w.is_current "
                "WHERE r.source_kind IN ('chat','work') AND (r.status IN ('queued','running','cancelling') OR "
                "(r.status IN ('succeeded','failed','cancelled') AND "
                "w.status IN ('scheduled','running','cancel_requested'))) "
                "ORDER BY r.updated_at,r.run_id LIMIT %s",
                (limit,),
            ).fetchall()
            return [
                ReconcileCandidate(
                    str(row["run_id"]), str(row["run_status"]), row["workflow_id"]
                )
                for row in rows
            ]

    @retryable_transaction
    def record_temporal_fact(self, run_id: UUID, status: str) -> None:
        if status not in ("completed", "failed", "cancelled", "terminated", "timed_out"):
            raise ValueError("status is not a Temporal terminal fact")
        with UnitOfWork(self.database_url) as uow:
            uow.execute(
                "UPDATE workflow_executions SET status=%s,closed_at=COALESCE(closed_at,now()),"
                "version=version+1,updated_at=now() WHERE run_id=%s AND is_current "
                "AND status IN ('scheduled','running','cancel_requested')",
                (status, run_id),
            )
