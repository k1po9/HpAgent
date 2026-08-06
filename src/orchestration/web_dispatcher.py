"""Outbox-to-Temporal dispatcher boundary for Web runs (D-03)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.service import RPCError, RPCStatusCode

from orchestration.web_workflow import (
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS,
    WebRunWorkflow,
    WebRunWorkflowInput,
)
from web_domain.outbox import (
    WEB_OUTBOX_RECOVERY_EVENT_TYPES,
    OutboxService,
)

logger = logging.getLogger("HpAgent.WebOutboxDispatcher")


def web_workflow_id(run_id: UUID | str) -> str:
    """The sole accepted Web Workflow ID derivation; never retry with a new ID."""
    return f"hpagent-web-run-{run_id}"


async def run_web_outbox_recovery_loop(
    outbox: OutboxService,
    lease_timeout_seconds: int,
    interval_seconds: float,
) -> None:
    """Periodically return expired ``processing`` Outbox leases to pending.

    An Outbox event stays in ``processing`` for exactly the lifetime of its
    claiming worker process.  If that process crashes before calling
    ``mark_processed``/``mark_retryable_failure``, the row would otherwise block
    every future consumer forever.  This loop reclaims rows whose lock is older
    than ``lease_timeout_seconds`` on a dedicated config-driven cadence.

    It deliberately runs independently of the fast Dispatcher poll (which wakes
    every ~250ms): recovery is a coarse, config-tuned sweep and must never be
    invoked with a non-positive timeout (``recover_expired(0)``).  Cancellation
    (worker shutdown) propagates so the caller can await the task.

    Only leases for the Web Dispatcher's own event types are recovered: this
    sweep must never steal ``processing`` rows owned by terminal/retain
    consumers.
    """
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            recovered = await asyncio.to_thread(
                outbox.recover_expired,
                lease_timeout_seconds,
                WEB_OUTBOX_RECOVERY_EVENT_TYPES,
            )
            if recovered:
                logger.info(
                    "Web Outbox lease recovery: %d expired lease(s) returned to pending",
                    recovered,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Web Outbox lease recovery iteration failed")


@dataclass(frozen=True)
class StartDecision:
    run_id: UUID
    workflow_id: str
    should_start: bool


class WorkflowExecutionStore(Protocol):
    def prepare_start(self, run_id: UUID) -> StartDecision: ...
    def still_queued(self, run_id: UUID) -> bool: ...
    def record_started(self, run_id: UUID, temporal_run_id: str) -> None: ...
    def needs_cancel(self, run_id: UUID) -> bool: ...
    def current_workflow_id(self, run_id: UUID) -> str | None: ...
    def record_cancel_requested(self, run_id: UUID) -> None: ...


class TemporalWebClient(Protocol):
    async def start_web_run(self, workflow_id: str, request: WebRunWorkflowInput) -> str: ...
    async def cancel_web_run(self, workflow_id: str) -> bool: ...


class CancellationFinalizer(Protocol):
    def finalize_cancelled(self, run_id: UUID): ...


class TemporalOutboxDispatcher:
    """Coordinates committed Outbox leases without holding a DB lock over RPC."""

    def __init__(
        self,
        store: WorkflowExecutionStore,
        temporal: TemporalWebClient,
        cancellation_finalizer: CancellationFinalizer | None = None,
    ):
        self.store = store
        self.temporal = temporal
        self.cancellation_finalizer = cancellation_finalizer

    async def dispatch_start(self, run_id: UUID) -> bool:
        # The store commits the scheduled execution record before this method
        # calls Temporal.  Every call below is therefore outside its DB txn.
        decision = await asyncio.to_thread(self.store.prepare_start, run_id)
        still_queued = await asyncio.to_thread(self.store.still_queued, run_id)
        if not decision.should_start or not still_queued:
            return False
        temporal_run_id = await self.temporal.start_web_run(
            decision.workflow_id, WebRunWorkflowInput(1, str(run_id))
        )
        await asyncio.to_thread(self.store.record_started, run_id, temporal_run_id)
        # The final check handles the unavoidable Start/Cancel narrow race.
        if await asyncio.to_thread(self.store.needs_cancel, run_id):
            if await self.temporal.cancel_web_run(decision.workflow_id):
                await asyncio.to_thread(self.store.record_cancel_requested, run_id)
        return True

    async def dispatch_cancel(self, run_id: UUID) -> bool:
        workflow_id = await asyncio.to_thread(self.store.current_workflow_id, run_id)
        if workflow_id is None:
            return False
        found = await self.temporal.cancel_web_run(workflow_id)
        if not found:
            if self.cancellation_finalizer is None:
                raise RuntimeError("cancelled Run has no Workflow and no lifecycle finalizer")
            await asyncio.to_thread(self.cancellation_finalizer.finalize_cancelled, run_id)
        else:
            await asyncio.to_thread(self.store.record_cancel_requested, run_id)
        return True


class TemporalClientAdapter:
    """Thin Temporal SDK adapter; retry policy is explicitly absent."""

    def __init__(self, client: Client):
        self.client = client

    async def start_web_run(self, workflow_id: str, request: WebRunWorkflowInput) -> str:
        try:
            handle = await self.client.start_workflow(
                WebRunWorkflow.run,
                request,
                id=workflow_id,
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                execution_timeout=timedelta(seconds=WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS),
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                retry_policy=None,
            )
            # ``start_workflow`` handles intentionally leave ``run_id`` unset
            # in the Python SDK. ``result_run_id`` is the execution created by
            # this Start call and is the value persisted in workflow_executions.
            temporal_run_id = handle.result_run_id
            if not temporal_run_id:
                raise RuntimeError("started Workflow has no Temporal Run ID")
            return str(temporal_run_id)
        except WorkflowAlreadyStartedError as exc:
            if exc.workflow_id != workflow_id or exc.workflow_type != "WebRunWorkflow":
                raise RuntimeError("deterministic Workflow ID belongs to another Workflow") from exc
            if exc.run_id:
                return str(exc.run_id)
            description = await self.client.get_workflow_handle(workflow_id).describe()
            recovered_run_id = description.raw_description.workflow_execution_info.execution.run_id
            if not recovered_run_id:
                raise RuntimeError("already-started Workflow has no Temporal Run ID") from exc
            return str(recovered_run_id)

    async def cancel_web_run(self, workflow_id: str) -> bool:
        try:
            await self.client.get_workflow_handle(workflow_id).cancel()
            return True
        except RPCError as exc:
            if exc.status == RPCStatusCode.NOT_FOUND:
                return False
            raise


class WebOutboxDispatcher:
    """Lease consumer for start/cancel events; external RPC is always post-commit."""

    def __init__(
        self,
        outbox: OutboxService,
        dispatcher: TemporalOutboxDispatcher,
        worker_id: str,
        *,
        max_attempts: int = 10,
    ):
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self.outbox = outbox
        self.dispatcher = dispatcher
        self.worker_id = worker_id
        self.max_attempts = max_attempts

    async def run_once(self, limit: int = 10) -> int:
        events = await asyncio.to_thread(
            self.outbox.claim, self.worker_id, {"start_run", "cancel_run"}, limit
        )
        for event in events:
            event_id = UUID(str(event["outbox_event_id"]))
            run_id = UUID(str(event["run_id"]))
            try:
                if event["event_type"] == "start_run":
                    await self.dispatcher.dispatch_start(run_id)
                else:
                    await self.dispatcher.dispatch_cancel(run_id)
                await asyncio.to_thread(
                    self.outbox.mark_processed, event_id, self.worker_id
                )
            except Exception as exc:
                if int(event["attempt_count"]) >= self.max_attempts:
                    await asyncio.to_thread(
                        self.outbox.dead_letter,
                        event_id,
                        self.worker_id,
                        "temporal_dispatch_exhausted",
                        str(exc)[:1000],
                    )
                else:
                    await asyncio.to_thread(
                        self.outbox.mark_retryable_failure,
                        event_id,
                        self.worker_id,
                        "temporal_dispatch_failed",
                        str(exc)[:1000],
                        datetime.now(UTC) + timedelta(seconds=5),
                    )
        return len(events)
