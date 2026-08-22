"""Database-only Web lifecycle Temporal Activities (D-02)."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from typing import Any
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_activities.store import AgentDataStore, LeaseConflict
from agent_execution.facade import StableExecutionFailure
from agent_execution.tracing import trace_end, trace_node_id, trace_start
from agent_execution.web_host import WebExecutionHost
from agent_workflows.contracts import AGENT_STRATEGIES
from common.logging import log_event
from orchestration.web_workflow import FailureInput, WebRunWorkflowInput
from web_domain.errors import DomainError
from web_domain.lifecycle import WebRunLifecycleService

from .durable_web_workflow import AcquireLeaseInput, ReleaseLeaseInput
from .web_workflow import WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS

lease_logger = logging.getLogger("HpAgent.ExecutionLease")

_lifecycle: WebRunLifecycleService | None = None
_web_host: WebExecutionHost | None = None
_agent_store: AgentDataStore | None = None
_agent_event_factory: Any | None = None
_agent_max_turns = 20


async def _lifecycle_call(function, *args):
    try:
        return await asyncio.to_thread(function, *args)
    except (DomainError, ValueError) as exc:
        raise ApplicationError(
            "Web lifecycle invariant rejected", non_retryable=True
        ) from exc


def inject_web_lifecycle(service: WebRunLifecycleService) -> None:
    global _lifecycle
    _lifecycle = service


def inject_web_execution_host(host: WebExecutionHost) -> None:
    global _web_host
    _web_host = host


def inject_agent_data_store(store: AgentDataStore, *, max_turns: int = 20) -> None:
    global _agent_store, _agent_max_turns
    if max_turns < 1:
        raise ValueError("max_turns must be positive")
    _agent_store = store
    _agent_max_turns = max_turns


def inject_agent_event_factory(factory: Any) -> None:
    global _agent_event_factory
    _agent_event_factory = factory


def _store() -> AgentDataStore:
    if _agent_store is None:
        raise RuntimeError("AgentDataStore was not injected")
    return _agent_store


def _service() -> WebRunLifecycleService:
    if _lifecycle is None:
        raise RuntimeError("WebRunLifecycleService was not injected")
    return _lifecycle


def _host() -> WebExecutionHost:
    if _web_host is None:
        raise RuntimeError("WebExecutionHost was not injected")
    return _web_host


def _heartbeat(details: dict[str, str | int]) -> None:
    """Allow direct unit invocation while retaining Temporal heartbeats in production."""
    try:
        activity.heartbeat(details)
    except RuntimeError:
        pass


async def _heartbeat_agent() -> None:
    while True:
        await asyncio.sleep(WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS)
        _heartbeat({"schema_version": 1, "phase": "running"})


@activity.defn
async def prepare_run_activity(request: WebRunWorkflowInput) -> dict[str, str]:
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
async def execute_agent_activity(request: WebRunWorkflowInput) -> dict[str, str]:
    """The sole owner of Web Agent execution; completion commits before return."""
    request.validate()
    _heartbeat({"schema_version": 1, "phase": "starting"})
    heartbeat_task = asyncio.create_task(_heartbeat_agent())
    try:
        try:
            await _host().execute(request.run_id)
        except StableExecutionFailure as exc:
            raise ApplicationError(
                exc.safe_message, type=exc.code, non_retryable=True
            ) from exc
    finally:
        heartbeat_task.cancel()
        await asyncio.gather(heartbeat_task, return_exceptions=True)
    _heartbeat({"schema_version": 1, "phase": "completed"})
    return {"run_id": request.run_id, "status": "completed"}


@activity.defn
async def acquire_execution_lease_activity(request: AcquireLeaseInput) -> dict[str, str | int]:
    if request.schema_version != 1 or not request.run_id:
        raise ApplicationError("invalid execution lease input", non_retryable=True)
    identity = await asyncio.to_thread(_store().run_identity, request.run_id)
    strategy = str(identity["agent_strategy"])
    if strategy not in AGENT_STRATEGIES:
        raise ApplicationError(
            "unsupported agent strategy",
            type="unsupported_agent_strategy",
            non_retryable=True,
        )
    fields = {
        "run_id": request.run_id,
        "execution_id": request.run_id,
        "account_id": identity["account_id"],
        "surface": "web",
        "strategy": strategy,
    }
    log_event(lease_logger, logging.INFO, "execution_lease_acquire_started", "lease", **fields, status="started")
    try:
        lease = await asyncio.to_thread(
            _store().acquire_lease, str(identity["account_id"]), request.run_id
        )
    except LeaseConflict:
        log_event(lease_logger, logging.INFO, "execution_lease_waiting", "lease", **fields, status="waiting")
        if _agent_event_factory is not None:
            events = _agent_event_factory.for_run(request.run_id)
            await events.progress(
                "waiting_for_account_execution",
                "正在等待同账号的另一个任务完成…",
            )
            await events.close()
        return {
            "run_id": request.run_id,
            "account_id": str(identity["account_id"]),
            "acquired": False,
        }
    result: dict[str, str | int] = {
        "run_id": request.run_id,
        "acquired": True,
        "account_id": str(identity["account_id"]),
        "conversation_id": str(identity["conversation_id"]),
        "session_id": str(identity["session_id"]),
        "trigger_message_id": str(identity["trigger_message_id"]),
        "strategy": strategy,
        "interaction_profile": "web_plan" if strategy == "plan_and_execute" else "web_chat",
        "fencing_token": lease.fencing_token,
        "lease_expires_at": lease.lease_expires_at,
        "max_turns": _agent_max_turns,
    }
    log_event(lease_logger, logging.INFO, "execution_lease_acquired", "lease", **fields, status="success", fencing_token=lease.fencing_token, lease_expires_at=lease.lease_expires_at)
    return result


@activity.defn
async def release_execution_lease_activity(request: ReleaseLeaseInput) -> dict[str, str | bool]:
    if request.schema_version != 1:
        raise ApplicationError("invalid execution lease input", non_retryable=True)
    released = await asyncio.to_thread(
        _store().release_lease,
        request.account_id,
        request.run_id,
        request.fencing_token,
    )
    log_event(
        lease_logger,
        logging.INFO,
        "execution_lease_released",
        "lease",
        run_id=request.run_id,
        execution_id=request.run_id,
        account_id=request.account_id,
        surface="web",
        fencing_token=request.fencing_token,
        status="success" if released else "stale",
    )
    return {"run_id": request.run_id, "released": released}


@activity.defn
async def finalize_failed_activity(failure: FailureInput) -> dict[str, str]:
    events = (
        _agent_event_factory.for_run(failure.run_id)
        if _agent_event_factory is not None
        else None
    )
    authority = asdict(await _lifecycle_call(
        _service().finalize_failed, UUID(failure.run_id), failure.error_code, failure.error_message
    ))
    await _finish_root_trace(
        failure.run_id,
        "failed",
        {"error_code": failure.error_code},
        events=events,
    )
    return authority


@activity.defn
async def finalize_cancelled_activity(request: WebRunWorkflowInput) -> dict[str, str]:
    request.validate()
    events = (
        _agent_event_factory.for_run(request.run_id)
        if _agent_event_factory is not None
        else None
    )
    authority = asdict(
        await _lifecycle_call(_service().finalize_cancelled, UUID(request.run_id))
    )
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
        await trace_start(
            events, root_node_id, None, "AgentExecution", "agent", {"surface": "web"}
        )
        await trace_end(events, root_node_id, status, metadata)
    finally:
        await events.close()
