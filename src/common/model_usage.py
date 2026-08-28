"""Canonical token usage shared by clients, tracing, and Run budgets."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from common.token_counter import estimate_messages_tokens, estimate_tokens


def canonical_model_usage(
    usage: Mapping[str, Any] | None,
    *,
    messages: list[dict[str, Any]] | None = None,
    output_text: str | None = None,
) -> dict[str, int | str]:
    """Normalize provider fields, or return an explainable local estimate."""
    raw = usage or {}
    input_value = raw.get("input_tokens", raw.get("prompt_tokens"))
    output_value = raw.get("output_tokens", raw.get("completion_tokens"))
    if _token_count(input_value) and _token_count(output_value):
        input_tokens = int(input_value)
        output_tokens = int(output_value)
        source = "provider"
    else:
        input_tokens = estimate_messages_tokens(messages or [])
        output_tokens = estimate_tokens(output_text or "")
        source = "estimated"
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "usage_source": source,
    }


def _token_count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
