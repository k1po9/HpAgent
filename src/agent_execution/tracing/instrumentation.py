from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("HpAgent.TraceInstrumentation")


def model_observation_metadata(decision: object) -> dict[str, Any]:
    """Extract operational model facts without response text or reasoning."""
    raw_response = getattr(decision, "raw_response", None)
    metadata: dict[str, Any] = {}
    for field in ("model", "provider", "endpoint_id"):
        value = getattr(raw_response, field, None)
        if value:
            metadata[field] = str(value)
    usage = getattr(raw_response, "usage", None)
    if isinstance(usage, Mapping):
        metadata["token_usage"] = dict(usage)
    return metadata


async def trace_start(
    events: object,
    node_id: str,
    parent_id: str | None,
    name: str,
    node_type: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    """Call an optional trace-capable EventSink without affecting execution."""
    method = getattr(events, "trace_start", None)
    if method is None:
        return
    try:
        await method(node_id, parent_id, name, node_type, metadata)
    except Exception:
        logger.exception(
            "Trace start projection failed",
            extra={
                "event": "trace_projection_failed",
                "component": "trace",
                "node_id": node_id,
                "status": "degraded",
                "error_code": "trace_start_failed",
            },
        )


async def trace_end(
    events: object,
    node_id: str,
    status: str,
    metadata: Mapping[str, Any] | None = None,
) -> None:
    method = getattr(events, "trace_end", None)
    if method is None:
        return
    try:
        await method(node_id, status, metadata)
    except Exception:
        logger.exception(
            "Trace end projection failed",
            extra={
                "event": "trace_projection_failed",
                "component": "trace",
                "node_id": node_id,
                "status": "degraded",
                "error_code": "trace_end_failed",
            },
        )
