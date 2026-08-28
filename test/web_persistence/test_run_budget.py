from __future__ import annotations

from uuid import uuid4

import pytest

from agent_execution.run_budget import RunBudgetExhausted, RunBudgetService
from web_domain.services import CommandService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def test_reserve_settle_and_temporal_replay_are_idempotent(
    db, account_id, database_url, worker_database_url,
) -> None:
    _service, _conversation_id, run_id = _conversation_and_run(
        database_url, account_id
    )
    budget = RunBudgetService(worker_database_url)
    operation_id = f"{run_id}:tool:call-1"
    reservation = {"tool_calls": 1, "bytes_scanned": 1024}

    assert budget.reserve(run_id, operation_id, reservation).replayed is False
    assert budget.reserve(run_id, operation_id, reservation).replayed is True
    assert budget.settle(
        run_id, operation_id,
        {"tool_calls": 1, "bytes_scanned": 17},
        "measured",
    ).replayed is False
    assert budget.settle(
        run_id, operation_id,
        {"tool_calls": 1, "bytes_scanned": 17},
        "measured",
    ).replayed is True

    row = db.execute(
        "SELECT used,reserved FROM run_budgets WHERE run_id=%s", (run_id,)
    ).fetchone()
    assert row[0]["tool_calls"] == 1
    assert row[0]["bytes_scanned"] == 17
    assert row[1]["tool_calls"] == 0
    assert row[1]["bytes_scanned"] == 0


def test_enforce_preserves_final_response_reserve(
    db, account_id, database_url, worker_database_url,
) -> None:
    service = CommandService(
        database_url,
        budget_mode="enforce",
        budget_limits={
            "model_input_tokens": 10,
            "model_output_tokens": 10,
            "model_total_tokens": 20,
            "model_calls": 1,
            "tool_calls": 1,
            "bytes_scanned": 1,
            "bytes_returned_to_model": 1,
            "bytes_written": 1,
            "output_file_bytes": 1,
            "wall_time_ms": 1,
        },
        final_response_reserve_tokens=4,
    )
    conversation = service.create_conversation(account_id, str(uuid4()))[
        "conversation_id"
    ]
    run_id = service.send_message(
        account_id, conversation, str(uuid4()), "hello"
    )["run_id"]
    budget = RunBudgetService(worker_database_url)

    with pytest.raises(RunBudgetExhausted):
        budget.reserve(
            run_id, "ordinary-model-call", {"model_output_tokens": 7}
        )
    final = budget.reserve(
        run_id, "final-model-call", {"model_output_tokens": 7},
        final_response=True,
    )
    assert final.state == "reserved"
