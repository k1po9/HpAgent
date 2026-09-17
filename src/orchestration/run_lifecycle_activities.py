"""Database-only canonical Run lifecycle Activities; adapters own source and delivery."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from typing import Any, Protocol
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_workflows.contracts import AgentRunInput
from orchestration.run_lifecycle_contracts import FailureInput, RunLifecycleInput
from tracing import trace_end, trace_node_id, trace_start
from web_domain.errors import DomainError


class RunLifecyclePort(Protocol):
    def prepare(
        self, run_id: UUID, workflow_id: str | None = None, temporal_run_id: str | None = None
    ) -> Any: ...
    def finalize_failed(self, run_id: UUID, error_code: str, error_message: str) -> Any: ...
    def finalize_cancelled(self, run_id: UUID) -> Any: ...


class RunInputLoader(Protocol):
    def load(self, run_id: str) -> AgentRunInput: ...


async def _lifecycle_call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except (DomainError, ValueError) as exc:
        raise ApplicationError("Run lifecycle invariant rejected", non_retryable=True) from exc


class RunLifecycleActivities:
    """Lifecycle Activity collection with explicit instance-owned dependencies."""

    def __init__(
        self,
        lifecycle: RunLifecyclePort,
        run_input_loader: RunInputLoader,
        event_factory: Any | None = None,
    ) -> None:
        self._lifecycle = lifecycle
        self._run_input_loader = run_input_loader
        self._event_factory = event_factory

    @activity.defn(name="prepare_run_activity")
    async def prepare_run(self, request: RunLifecycleInput) -> dict[str, str]:
        request.validate()
        try:
            info = activity.info()
        except RuntimeError:
            info = None
        if info is not None:
            authority = await _lifecycle_call(
                self._lifecycle.prepare,
                UUID(request.run_id),
                info.workflow_id,
                info.workflow_run_id,
            )
        else:
            authority = await _lifecycle_call(
                self._lifecycle.prepare, UUID(request.run_id)
            )
        return asdict(authority)

    @activity.defn(name="finalize_failed_activity")
    async def finalize_failed(self, failure: FailureInput) -> dict[str, str]:
        events = (
            self._event_factory.for_run(failure.run_id)
            if self._event_factory is not None
            else None
        )
        authority = asdict(
            await _lifecycle_call(
                self._lifecycle.finalize_failed,
                UUID(failure.run_id),
                failure.error_code,
                failure.error_message,
            )
        )
        await _finish_root_trace(
            failure.run_id,
            "failed",
            {"error_code": failure.error_code},
            events=events,
        )
        return authority

    @activity.defn(name="finalize_cancelled_activity")
    async def finalize_cancelled(self, request: RunLifecycleInput) -> dict[str, str]:
        request.validate()
        events = (
            self._event_factory.for_run(request.run_id)
            if self._event_factory is not None
            else None
        )
        authority = asdict(
            await _lifecycle_call(
                self._lifecycle.finalize_cancelled, UUID(request.run_id)
            )
        )
        await _finish_root_trace(request.run_id, "cancelled", events=events)
        return authority

    @activity.defn(name="load_agent_run_input_activity")
    async def load_agent_run_input(self, request: RunLifecycleInput) -> AgentRunInput:
        request.validate()
        return await asyncio.to_thread(self._run_input_loader.load, request.run_id)


async def _finish_root_trace(
    run_id: str,
    status: str,
    metadata: dict[str, Any] | None = None,
    *,
    events: Any = None,
) -> None:
    if events is None:
        return
    root_node_id = trace_node_id(run_id, "agent_execution")
    try:
        await trace_start(events, root_node_id, None, "AgentExecution", "agent", {})
        await trace_end(events, root_node_id, status, metadata)
    finally:
        await events.close()
