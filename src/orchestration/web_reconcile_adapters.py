"""PostgreSQL adapter that deliberately reuses WebRunLifecycleService."""
from __future__ import annotations

from uuid import UUID

from web_domain.lifecycle import WebRunLifecycleService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore


class LifecycleReconcileStore:
    def __init__(self, executions: PostgresWorkflowExecutionStore, lifecycle: WebRunLifecycleService):
        self._executions = executions
        self._lifecycle = lifecycle

    def candidates(self, limit: int):
        return self._executions.reconcile_candidates(limit)

    def finalize_failed(self, run_id: str, code: str, message: str) -> None:
        self._lifecycle.finalize_failed(UUID(run_id), code, message)

    def finalize_cancelled(self, run_id: str) -> None:
        self._lifecycle.finalize_cancelled(UUID(run_id))

    def record_temporal_fact(self, run_id: str, status: str) -> None:
        self._executions.record_temporal_fact(UUID(run_id), status)
