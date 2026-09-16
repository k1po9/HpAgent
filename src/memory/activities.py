"""Activities for scheduled long-term memory maintenance."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, cast

from temporalio import activity

_memory_reflection: Any = None
_metrics: Any = None


def inject_scheduled_services(*, memory_reflection: Any, metrics: Any) -> None:
    global _memory_reflection, _metrics
    _memory_reflection, _metrics = memory_reflection, metrics


def _required(value: Any, name: str) -> Any:
    if value is None:
        raise RuntimeError(f"{name} was not injected")
    return value


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
