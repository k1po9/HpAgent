"""Reconcile domain Run truth with Temporal facts without holding DB locks over RPC."""

from __future__ import annotations

import asyncio

from temporalio.client import Client, WorkflowExecutionStatus
from temporalio.service import RPCError, RPCStatusCode

from orchestration.run_lifecycle_contracts import (
    ReconcileCandidate as ReconcileCandidate,
)
from orchestration.run_lifecycle_contracts import (
    ReconcileStore as ReconcileStore,
)
from orchestration.run_lifecycle_contracts import (
    TemporalFact as TemporalFact,
)
from orchestration.run_lifecycle_contracts import (
    TemporalInspector as TemporalInspector,
)


class WebRunReconciler:
    """A candidate is read in a short transaction; Temporal is queried outside it."""

    def __init__(self, store: ReconcileStore, temporal: TemporalInspector):
        self._store = store
        self._temporal = temporal

    async def run_once(self, limit: int = 100) -> int:
        candidates = await asyncio.to_thread(self._store.candidates, limit)
        for item in candidates:
            if not item.workflow_id:
                if item.run_status == "queued":
                    continue
                await asyncio.to_thread(
                    self._store.finalize_failed,
                    item.run_id,
                    "terminal_commit_missing",
                    "执行记录缺失。",
                )
                continue
            fact = await self._temporal.describe(item.workflow_id)
            if fact.status not in ("open", "not_found"):
                await asyncio.to_thread(self._store.record_temporal_fact, item.run_id, fact.status)
            if item.run_status in ("succeeded", "failed", "cancelled"):
                if fact.status == "open":
                    await self._temporal.cancel(item.workflow_id)
                continue
            if item.run_status == "cancelling" and fact.status != "open":
                await asyncio.to_thread(self._store.finalize_cancelled, item.run_id)
                continue
            if fact.status in ("failed", "timed_out", "terminated"):
                code = {
                    "failed": "internal_execution_error",
                    "timed_out": "run_timeout",
                    "terminated": "workflow_terminated",
                }[fact.status]
                await asyncio.to_thread(
                    self._store.finalize_failed, item.run_id, code, "执行未能完成。"
                )
            elif fact.status == "completed":
                await asyncio.to_thread(
                    self._store.finalize_failed,
                    item.run_id,
                    "terminal_commit_missing",
                    "终态提交缺失。",
                )
            elif fact.status == "cancelled":
                await asyncio.to_thread(self._store.finalize_cancelled, item.run_id)
            elif fact.status == "open" and item.run_status == "cancelling":
                await self._temporal.cancel(item.workflow_id)
            elif fact.status == "not_found" and item.run_status == "cancelling":
                await asyncio.to_thread(self._store.finalize_cancelled, item.run_id)
            elif fact.status == "not_found" and item.run_status == "running":
                await asyncio.to_thread(
                    self._store.finalize_failed,
                    item.run_id,
                    "terminal_commit_missing",
                    "Temporal 执行记录缺失。",
                )
        return len(candidates)


class TemporalInspectorAdapter:
    def __init__(self, client: Client):
        self._client = client

    async def describe(self, workflow_id: str) -> TemporalFact:
        try:
            description = await self._client.get_workflow_handle(workflow_id).describe()
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return TemporalFact("not_found")
            raise
        return TemporalFact(
            {
                WorkflowExecutionStatus.RUNNING: "open",
                WorkflowExecutionStatus.COMPLETED: "completed",
                WorkflowExecutionStatus.FAILED: "failed",
                WorkflowExecutionStatus.CANCELED: "cancelled",
                WorkflowExecutionStatus.TERMINATED: "terminated",
                WorkflowExecutionStatus.TIMED_OUT: "timed_out",
                WorkflowExecutionStatus.CONTINUED_AS_NEW: "open",
            }[description.status]
        )

    async def cancel(self, workflow_id: str) -> None:
        await self._client.get_workflow_handle(workflow_id).cancel()
