"""Stable Web Run failure policy shared by commands and DTO projections."""
from __future__ import annotations

NON_RETRYABLE_FAILURE_CODES = frozenset(
    {
        "tool_side_effect_uncertain",
        "side_effect_reconciliation_failed",
    }
)


def is_failure_retryable(failure_code: str | None) -> bool:
    return failure_code not in NON_RETRYABLE_FAILURE_CODES
