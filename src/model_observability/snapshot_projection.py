"""Entitlement-controlled projections of stored provider request bodies."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

MINIMAL_FIELDS = frozenset({"snapshot_id", "content_hash", "model_call_id"})
SUMMARY_FIELDS = frozenset({
    "snapshot_id", "content_hash", "model_call_id", "phase", "fallback_attempt",
    "endpoint_id", "provider", "model", "api_format", "created_at", "message_count",
    "tool_count",
})


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
    }


def summary_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    body = row["provider_request_body"]
    if not isinstance(body, Mapping):
        raise ValueError("canonical provider request body must be an object")
    return {
        **minimal_projection(row),
        "phase": str(row["phase"]),
        "fallback_attempt": int(row["fallback_attempt"]),
        "endpoint_id": str(row["endpoint_id"]),
        "provider": str(row["provider"]),
        "model": str(row["model"]),
        "api_format": str(row["api_format"]),
        "created_at": _timestamp(row["created_at"]),
        "message_count": _count(body, "messages", "contents", "input"),
        "tool_count": _count(body, "tools", "functions"),
    }


def full_safe_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return {**summary_projection(row), "provider_request_body": row["provider_request_body"]}
