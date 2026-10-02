"""Entitlement-controlled projections of stored provider request bodies."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

MINIMAL_FIELDS = frozenset({"snapshot_id", "content_hash", "model_call_id", "dispatch_status"})
SUMMARY_FIELDS = frozenset({
    "snapshot_id", "content_hash", "model_call_id", "phase", "fallback_attempt",
    "endpoint_id", "provider", "model", "api_format", "created_at", "message_count",
    "tool_count", "resolved_url", "dispatch_status",
    "work_id", "requirement_revision", "execution_id", "dispatched_at",
})


def _dispatch_status(row: Mapping[str, Any]) -> str:
    state = row.get("usage_state")
    if row.get("dispatched_at") is not None and state in (None, "reserved", "released"):
        return "dispatched_or_uncertain"
    if state is None or state == "released":
        return "not_dispatched"
    if state == "reserved":
        return "reserved_or_in_flight"
    if state == "settled" and row.get("usage_source") in {"provider", "measured"}:
        return "succeeded"
    return "uncertain"


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _count(body: Mapping[str, Any], *keys: str) -> int:
    for key in keys:
        value = body.get(key)
        if isinstance(value, list):
            return len(value)
    return 0


def minimal_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "snapshot_id": str(row["snapshot_id"]),
        "content_hash": bytes(row["content_hash"]).hex(),
        "model_call_id": str(row["model_call_id"]),
        "dispatch_status": _dispatch_status(row),
    }


def summary_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    body = row["provider_request_body"]
    if not isinstance(body, Mapping):
        raise ValueError("canonical provider request body must be an object")
    return {
        **minimal_projection(row),
        "work_id": str(row["work_id"]) if row.get("work_id") else None,
        "requirement_revision": row.get("requirement_revision"),
        "execution_id": str(row["execution_id"]) if row.get("execution_id") else None,
        "dispatched_at": _timestamp(row["dispatched_at"]) if row.get("dispatched_at") else None,
        "phase": str(row["phase"]),
        "fallback_attempt": int(row["fallback_attempt"]),
        "endpoint_id": str(row["endpoint_id"]),
        "provider": str(row["provider"]),
        "model": str(row["model"]),
        "api_format": str(row["api_format"]),
        "resolved_url": row.get("resolved_url"),
        "dispatch_status": _dispatch_status(row),
        "created_at": _timestamp(row["created_at"]),
        "message_count": _count(body, "messages", "contents", "input"),
        "tool_count": _count(body, "tools", "functions"),
    }


def full_safe_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**summary_projection(row), "provider_request_body": row["provider_request_body"]}
