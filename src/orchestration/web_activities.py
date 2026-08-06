"""Database-only Web lifecycle Temporal Activities (D-02)."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
from uuid import UUID

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_execution.facade import StableExecutionFailure
from agent_execution.web_host import WebExecutionHost
from orchestration.web_workflow import FailureInput, WebRunWorkflowInput
from web_domain.errors import DomainError
from web_domain.lifecycle import WebRunLifecycleService

from .web_workflow import WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS

_lifecycle: WebRunLifecycleService | None = None
_web_host: WebExecutionHost | None = None


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
async def finalize_failed_activity(failure: FailureInput) -> dict[str, str]:
    return asdict(await _lifecycle_call(
        _service().finalize_failed, UUID(failure.run_id), failure.error_code, failure.error_message
    ))


@activity.defn
async def finalize_cancelled_activity(request: WebRunWorkflowInput) -> dict[str, str]:
    request.validate()
    return asdict(await _lifecycle_call(_service().finalize_cancelled, UUID(request.run_id)))
