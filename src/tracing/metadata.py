"""Allowlisted, size-bounded metadata for persisted and streamed traces."""
from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

TRACE_METADATA_SCHEMA_VERSION = 1
TRACE_METADATA_MAX_BYTES = 4096
_STRING_MAX_CHARS = 256

_COMMON = frozenset({"schema_version", "error_code", "operation_id", "deduplicated"})
_POLICIES: dict[str, frozenset[str]] = {
    "AgentExecution": frozenset({"strategy", "surface", "terminal_fallback", "tool_turns"}),
    "ContextAssembly": frozenset({"message_count", "memory_count"}),
    "MemoryQueryRewrite": frozenset({"model_selector", "model_invoked"}),
    "MemoryRecall": frozenset({"memory_count"}),
    "LLMCall": frozenset({
        "turn", "phase", "model_selector", "plan_id", "plan_version", "step_id",
        "stop_reason", "tool_count", "estimated_input_tokens", "model", "provider",
        "endpoint_id", "token_usage", "step_count",
    }),
    "ToolExecution": frozenset({
        "tool_name", "tool_call_id", "turn", "side_effect_class", "result_ref",
    }),
    "FileIngestion": frozenset({
        "file_id_suffix", "size_bytes", "media_type", "result_code", "duration_ms",
    }),
    "WorkspacePrepare": frozenset({"input_count", "isolation_mode", "duration_ms"}),
    "FileInspect": frozenset({"scanned_bytes", "returned_bytes", "truncated"}),
    "FileSearch": frozenset({
        "query_mode", "match_count", "scanned_bytes", "returned_bytes", "truncated",
    }),
    "FileCount": frozenset({"count", "scanned_bytes"}),
    "FileTransform": frozenset({
        "operation_type", "input_bytes", "output_bytes", "operation_id_suffix",
    }),
    "OutputPublish": frozenset({"file_id_suffix", "size_bytes", "publish_status"}),
    "BudgetCheck": frozenset({"dimension", "used", "limit", "decision"}),
}
_TOKEN_USAGE_KEYS = frozenset({
    "input_tokens", "output_tokens", "total_tokens", "usage_source"
})
_RUN_KEYS = frozenset({"schema_version", "source", "strategy", "error_code"})


def sanitize_trace_metadata(
    node_name: str, metadata: Mapping[str, Any] | None
) -> dict[str, Any]:
    """Drop unknown/sensitive keys and retain only bounded JSON scalars."""
    allowed = _COMMON | _POLICIES.get(node_name, frozenset())
    safe: dict[str, Any] = {"schema_version": TRACE_METADATA_SCHEMA_VERSION}
    for key, value in (metadata or {}).items():
        if key not in allowed or key == "schema_version":
            continue
        normalized = _safe_value(key, value)
        if normalized is not None:
            safe[key] = normalized
    _require_bounded(safe)
    return safe


def sanitize_trace_run_metadata(
    metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    safe: dict[str, Any] = {"schema_version": TRACE_METADATA_SCHEMA_VERSION}
    for key, value in (metadata or {}).items():
        if key not in _RUN_KEYS or key == "schema_version":
            continue
        normalized = _safe_value(key, value)
        if normalized is not None:
            safe[key] = normalized
    _require_bounded(safe)
    return safe


def _safe_value(key: str, value: Any) -> Any | None:
    if key == "token_usage":
        if not isinstance(value, Mapping):
            return None
        return {
            item: normalized
            for item in _TOKEN_USAGE_KEYS
            if (normalized := _safe_scalar(value.get(item))) is not None
        }
    return _safe_scalar(value)


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if value == value and abs(value) != float("inf") else None
    if isinstance(value, str):
        return value[:_STRING_MAX_CHARS]
    return None


def _require_bounded(metadata: Mapping[str, Any]) -> None:
    size = len(
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    if size > TRACE_METADATA_MAX_BYTES:
        raise ValueError("trace metadata exceeds the hard byte limit")
