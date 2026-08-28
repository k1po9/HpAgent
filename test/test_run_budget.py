from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_execution.model_budget_context import model_budget_scope
from agent_execution.run_budget import DIMENSIONS, RunBudgetConflict, _amounts
from common.errors import ModelAPIError
from common.model_usage import canonical_model_usage
from common.types import ModelResponse
from resources.resource_pool import ResourcePool


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


@pytest.mark.asyncio
async def test_provider_fallback_attempts_are_accounted_separately(monkeypatch) -> None:
    calls: list[tuple] = []

    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("resources.resource_pool.asyncio.to_thread", direct)

    class Budget:
        def reserve(self, run_id, operation_id, values, **kwargs):
            calls.append(("reserve", run_id, operation_id, values, kwargs))
            return SimpleNamespace(replayed=False, state="reserved")

        def settle(self, run_id, operation_id, values, source):
            calls.append(("settle", run_id, operation_id, values, source))

        def release(self, run_id, operation_id):
            calls.append(("release", run_id, operation_id))

    class Client:
        _max_tokens = 20
        model = "test-model"
        provider = "test"

        def __init__(self, failure: bool):
            self.failure = failure

        async def generate(self, **kwargs):
            if self.failure:
                raise ModelAPIError("unavailable")
            return ModelResponse(
                content="ok",
                usage={
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                    "usage_source": "provider",
                },
            )

    pool = ResourcePool(SimpleNamespace())
    pool._model_clients = {
        "primary": {"client": Client(True)},
        "fallback": {"client": Client(False)},
    }
    pool._fallback_groups = {"chat": ["primary", "fallback"]}

    with model_budget_scope(
        Budget(), "run-1", "decision-1", final_response=True
    ):
        result = await pool.generate(
            [{"role": "user", "content": "hello"}], model_selector="chat"
        )

    assert result.content == "ok"
    reserves = [item for item in calls if item[0] == "reserve"]
    settlements = [item for item in calls if item[0] == "settle"]
    assert len(reserves) == 2
    assert reserves[0][2] != reserves[1][2]
    assert all(item[4] == {"final_response": True} for item in reserves)
    assert settlements[0][-1] == "estimated"
    assert settlements[1][3] == {
        "model_input_tokens": 3,
        "model_output_tokens": 2,
        "model_total_tokens": 5,
        "model_calls": 1,
    }
    assert settlements[1][-1] == "provider"


@pytest.mark.asyncio
async def test_settled_activity_retry_does_not_settle_usage_twice(monkeypatch) -> None:
    calls: list[tuple] = []

    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("resources.resource_pool.asyncio.to_thread", direct)

    class Budget:
        def reserve(self, *args, **kwargs):
            calls.append(("reserve", args, kwargs))
            return SimpleNamespace(replayed=True, state="settled")

        def settle(self, *args, **kwargs):
            calls.append(("settle", args, kwargs))

    class Client:
        _max_tokens = 20
        model = "test-model"
        provider = "test"

        async def generate(self, **kwargs):
            return ModelResponse(
                content="recovered",
                usage={
                    "input_tokens": 3,
                    "output_tokens": 2,
                    "total_tokens": 5,
                    "usage_source": "provider",
                },
            )

    pool = ResourcePool(SimpleNamespace())
    pool._model_clients = {"primary": {"client": Client()}}
    with model_budget_scope(Budget(), "run-1", "decision-1"):
        result = await pool.generate(
            [{"role": "user", "content": "hello"}], model_selector="primary"
        )

    assert result.content == "recovered"
    assert [item[0] for item in calls] == ["reserve"]
