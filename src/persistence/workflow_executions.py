"""Workflow fact SQL; callers own Run authority and transaction boundaries."""

from __future__ import annotations

from uuid import UUID

from persistence.uow import UnitOfWork


class WorkflowExecutionRepository:
    def has_current(self, uow: UnitOfWork, run_id: UUID) -> bool:
        return (
            uow.execute(
                "SELECT 1 FROM workflow_executions WHERE run_id=%s AND is_current", (run_id,)
            ).fetchone()
            is not None
        )

    def insert_scheduled(self, uow, record_id, account_id, conversation_id, run_id, workflow_id):
        uow.execute(
            "INSERT INTO workflow_executions(workflow_execution_id,account_id,conversation_id,run_id,workflow_id,execution_id) SELECT %s,%s,%s,%s,%s,execution_id FROM run_executions WHERE account_id=%s AND run_id=%s AND role='root'",
            (record_id, account_id, conversation_id, run_id, workflow_id, account_id, run_id),
        )

    def record_started(self, uow, temporal_run_id, run_id):
        uow.execute(
            "UPDATE workflow_executions SET temporal_run_id=%s,status='running',started_at=now(),version=version+1,updated_at=now() WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
            (temporal_run_id, run_id),
        )

    def record_cancel_requested(self, uow, run_id):
        uow.execute(
            "UPDATE workflow_executions SET status='cancel_requested',version=version+1,updated_at=now() WHERE run_id=%s AND is_current AND status IN ('scheduled','running')",
            (run_id,),
        )

    def record_terminal_fact(self, uow, status, run_id):
        uow.execute(
            "UPDATE workflow_executions SET status=%s,closed_at=COALESCE(closed_at,now()),version=version+1,updated_at=now() WHERE run_id=%s AND is_current AND status IN ('scheduled','running','cancel_requested')",
            (status, run_id),
        )

    def lock_current(self, uow, run_id):
        return uow.execute(
            "SELECT workflow_id,temporal_run_id,status FROM workflow_executions WHERE run_id=%s AND is_current FOR UPDATE",
            (run_id,),
        ).fetchone()

    def current_workflow(self, uow, run_id):
        return uow.execute(
            "SELECT workflow_id FROM workflow_executions WHERE run_id=%s AND is_current", (run_id,)
        ).fetchone()

    def reconcile_candidates(self, uow, limit):
        return uow.execute(
            "SELECT r.run_id,r.status AS run_status,w.workflow_id FROM runs r LEFT JOIN workflow_executions w ON w.run_id=r.run_id AND w.is_current WHERE r.source_kind IN ('chat','work') AND (r.status IN ('queued','running','cancelling') OR (r.status IN ('succeeded','failed','cancelled') AND w.status IN ('scheduled','running','cancel_requested'))) ORDER BY r.updated_at,r.run_id LIMIT %s",
            (limit,),
        ).fetchall()
