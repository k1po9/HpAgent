"""Temporal Activities for QQ execution and scheduled application services."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, cast

from temporalio import activity

_qq_execution_host: Any = None
_session_archive: Any = None
_memory_reflection: Any = None
_metrics: Any = None


def inject_services(
    *,
    qq_execution_host: Any,
    session_archive: Any,
    memory_reflection: Any,
    metrics: Any,
) -> None:
    """Inject frozen Activity dependencies before the Worker starts."""
    if qq_execution_host is None:
        raise RuntimeError("QQExecutionHost is required")
    global _qq_execution_host, _session_archive, _memory_reflection, _metrics
    _qq_execution_host = qq_execution_host
    _session_archive = session_archive
    _memory_reflection = memory_reflection
    _metrics = metrics


def _required(value: Any, name: str) -> Any:
    if value is None:
        raise RuntimeError(f"{name} was not injected")
    return value


@activity.defn
async def process_turn_activity(user_message: Dict[str, Any]) -> Dict[str, Any]:
    """Execute one QQ turn through the shared AgentExecutionFacade."""
    logger = logging.getLogger("HpAgent.Activity")
    session_id = user_message.get("session_id", "?")
    started_at = time.monotonic()
    try:
        try:
            workflow_id = activity.info().workflow_id
        except RuntimeError:
            workflow_id = f"direct-{session_id}"
        if not workflow_id:
            raise RuntimeError("QQ Activity has no Workflow identity")
        host = _required(_qq_execution_host, "QQExecutionHost")
        return cast(Dict[str, Any], await host.execute(workflow_id, user_message))
    except Exception:
        logger.exception(
            "process_turn_activity FAILED sid=%s latency=%.0fms",
            session_id,
            (time.monotonic() - started_at) * 1000,
        )
        raise


@activity.defn
async def archive_session_activity(session_id: str) -> Dict[str, Any]:
    """Archive a completed QQ session through SessionArchiveService."""
    service = _required(_session_archive, "SessionArchiveService")
    account_id = await service.get_session_account(session_id)
    if not account_id:
        return {"ok": False, "error": f"Session not found: {session_id}"}
    try:
        return cast(Dict[str, Any], await service.archive(session_id, account_id))
    except Exception as exc:
        logging.getLogger("HpAgent.Activity").exception(
            "archive_session_activity FAILED sid=%s", session_id
        )
        return {"ok": False, "error": str(exc)}


@activity.defn
async def reflect_activity(account_id: str) -> Dict[str, Any]:
    service = _required(_memory_reflection, "MemoryReflectionService")
    return cast(Dict[str, Any], await service.reflect(account_id))


@activity.defn
async def reflect_batch_activity(account_ids: List[str]) -> Dict[str, Any]:
    service = _required(_memory_reflection, "MemoryReflectionService")
    return cast(Dict[str, Any], await service.reflect_batch(account_ids))


@activity.defn
async def metrics_report_activity() -> Dict[str, Any]:
    service = _required(_metrics, "MetricsSnapshotService")
    snapshot = await service.snapshot()
    logging.getLogger("HpAgent.Metrics").info(
        "HindsightMetrics|%s",
        json.dumps(snapshot, ensure_ascii=False, default=str),
    )
    return cast(Dict[str, Any], snapshot)
