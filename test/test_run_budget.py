from __future__ import annotations

import pytest

from agent_execution.run_budget import DIMENSIONS, RunBudgetConflict, _amounts
from common.model_usage import canonical_model_usage


def test_canonical_usage_normalizes_both_provider_dialects() -> None:
    assert canonical_model_usage({"input_tokens": 10, "output_tokens": 4}) == {
        "input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
        "usage_source": "provider",
    }
    assert canonical_model_usage({"prompt_tokens": 7, "completion_tokens": 3}) == {
        "input_tokens": 7, "output_tokens": 3, "total_tokens": 10,
        "usage_source": "provider",
    }


def test_canonical_usage_estimates_when_provider_usage_is_missing() -> None:
    result = canonical_model_usage(
        None,
        messages=[{"role": "user", "content": "hello world"}],
        output_text="done",
    )
    assert result["input_tokens"] > 0
    assert result["output_tokens"] > 0
    assert result["total_tokens"] == result["input_tokens"] + result["output_tokens"]
    assert result["usage_source"] == "estimated"


def test_budget_dimensions_and_amounts_fail_closed() -> None:
    assert "tool_calls" in DIMENSIONS
    assert _amounts({"tool_calls": 1}) == {"tool_calls": 1}
    with pytest.raises(ValueError, match="unsupported"):
        _amounts({"made_up": 1})
    with pytest.raises(ValueError, match="non-negative"):
        _amounts({"tool_calls": -1})
    with pytest.raises(ValueError, match="non-negative"):
        _amounts({"tool_calls": True})


def test_budget_conflicts_expose_a_stable_code() -> None:
    assert RunBudgetConflict.code == "run_budget_operation_conflict"
