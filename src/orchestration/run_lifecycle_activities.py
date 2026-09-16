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


_lifecycle: RunLifecyclePort | None = None
_run_input_loader: RunInputLoader | None = None
_agent_event_factory: Any | None = None


async def _lifecycle_call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except (DomainError, ValueError) as exc:
        raise ApplicationError("Run lifecycle invariant rejected", non_retryable=True) from exc


def inject_run_lifecycle(service: RunLifecyclePort) -> None:
    global _lifecycle
    _lifecycle = service


def inject_agent_run_loader(loader: RunInputLoader) -> None:
    global _run_input_loader
    _run_input_loader = loader


def inject_agent_event_factory(factory: Any) -> None:
    global _agent_event_factory
    _agent_event_factory = factory


def _service() -> RunLifecyclePort:
    if _lifecycle is None:
        raise RuntimeError("Run lifecycle was not injected")
    return _lifecycle


@activity.defn
async def prepare_run_activity(request: RunLifecycleInput) -> dict[str, str]:
    request.validate()
    try:
        info = activity.info()
    except RuntimeError:
        info = None
    if info is not None:
        authority = await _lifecycle_call(
            _service().prepare,
            UUID(request.run_id),
            info.workflow_id,
            info.workflow_run_id,
        )
    else:
        # Direct unit invocation has no Temporal Activity context.
        authority = await _lifecycle_call(_service().prepare, UUID(request.run_id))
    return asdict(authority)


@activity.defn
async def finalize_failed_activity(failure: FailureInput) -> dict[str, str]:
    events = (
        _agent_event_factory.for_run(failure.run_id) if _agent_event_factory is not None else None
    )
    authority = asdict(
        await _lifecycle_call(
            _service().finalize_failed,
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


@activity.defn
async def finalize_cancelled_activity(request: RunLifecycleInput) -> dict[str, str]:
    request.validate()
    events = (
        _agent_event_factory.for_run(request.run_id) if _agent_event_factory is not None else None
    )
    authority = asdict(await _lifecycle_call(_service().finalize_cancelled, UUID(request.run_id)))
    await _finish_root_trace(request.run_id, "cancelled", events=events)
    return authority


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


@activity.defn
async def load_agent_run_input_activity(request: RunLifecycleInput) -> AgentRunInput:
    request.validate()
    if _run_input_loader is None:
        raise RuntimeError("Run input loader was not injected")
    return await asyncio.to_thread(_run_input_loader.load, request.run_id)
