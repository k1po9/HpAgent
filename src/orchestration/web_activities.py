"""Database-only Web lifecycle Temporal Activities (D-02)."""
from __future__ import annotations

import asyncio
import logging

from temporalio import activity
from temporalio.exceptions import ApplicationError

from agent_execution.web_host import WebExecutionHost
from application.execution_contracts import StableExecutionFailure
from orchestration.run_lifecycle_contracts import RunLifecycleInput as WebRunWorkflowInput

from .run_lifecycle_contracts import WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS

lease_logger = logging.getLogger("HpAgent.ExecutionLease")

_web_host: WebExecutionHost | None = None


def inject_web_execution_host(host: WebExecutionHost) -> None:
    global _web_host
    _web_host = host


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
async def execute_agent_activity(request: WebRunWorkflowInput) -> dict[str, str]:
    """Legacy Web execution Activity; unregistered pending W3 retirement."""
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
