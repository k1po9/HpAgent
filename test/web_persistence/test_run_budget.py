from __future__ import annotations

from uuid import uuid4

import pytest

from agent_execution.model_budget_context import current_model_budget, model_budget_scope
from agent_execution.run_budget import RunBudgetExhausted, RunBudgetService
from persistence.uow import UnitOfWork
from web_domain.run_usage_projection import load_run_budget_projection
from web_domain.services import CommandService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def _provider_attempt_id(run_id: str, execution_attempt: int) -> str:
    with model_budget_scope(
        None, run_id, f"{run_id}:decision", execution_attempt=execution_attempt,
    ):
        context = current_model_budget()
        assert context is not None
        return context.next_operation_id(1, "primary")


def _model_reservation() -> dict[str, int]:
    return {
        "model_input_tokens": 20, "model_output_tokens": 10,
        "model_total_tokens": 30, "model_calls": 1,
    }


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


def test_model_provider_activity_attempts_settle_independently(
    db, account_id, database_url, worker_database_url,
) -> None:
    _service, _conversation_id, run_id = _conversation_and_run(database_url, account_id)
    budget = RunBudgetService(worker_database_url)
    first = _provider_attempt_id(str(run_id), 1)
    second = _provider_attempt_id(str(run_id), 2)
    assert first != second

    budget.reserve(run_id, first, _model_reservation())
    budget.settle(run_id, first, {
        "model_input_tokens": 8, "model_output_tokens": 2,
        "model_total_tokens": 10, "model_calls": 1,
    }, "provider")
    budget.reserve(run_id, second, _model_reservation())
    budget.settle(run_id, second, {
        "model_input_tokens": 9, "model_output_tokens": 3,
        "model_total_tokens": 12, "model_calls": 1,
    }, "provider")

    aggregate = db.execute(
        "SELECT used FROM run_budgets WHERE run_id=%s", (run_id,)
    ).fetchone()[0]
    assert aggregate["model_input_tokens"] == 17
    assert aggregate["model_output_tokens"] == 5
    assert aggregate["model_total_tokens"] == 22
    assert aggregate["model_calls"] == 2
    states = dict(db.execute(
        "SELECT operation_id,state FROM run_usage_ledger "
        "WHERE run_id=%s AND dimension='model_calls'", (run_id,),
    ).fetchall())
    assert states[first] == states[second] == "settled"


def test_released_model_attempt_is_unmetered_and_not_consumed(
    db, account_id, database_url, worker_database_url,
) -> None:
    _service, _conversation_id, run_id = _conversation_and_run(database_url, account_id)
    budget = RunBudgetService(worker_database_url)
    failed = _provider_attempt_id(str(run_id), 1)
    succeeded = _provider_attempt_id(str(run_id), 2)

    budget.reserve(run_id, failed, _model_reservation())
    budget.release(run_id, failed)
    budget.reserve(run_id, succeeded, _model_reservation())
    budget.settle(run_id, succeeded, {
        "model_input_tokens": 9, "model_output_tokens": 3,
        "model_total_tokens": 12, "model_calls": 1,
    }, "provider")

    aggregate = db.execute(
        "SELECT used,reserved FROM run_budgets WHERE run_id=%s", (run_id,)
    ).fetchone()
    assert aggregate[0]["model_total_tokens"] == 12
    assert aggregate[0]["model_calls"] == 1
    assert aggregate[1]["model_total_tokens"] == 0
    assert aggregate[1]["model_calls"] == 0
    with UnitOfWork(database_url) as uow:
        projection = load_run_budget_projection(uow, run_id)
    assert projection is not None
    assert projection["model_calls"]["settled"] == 1
    assert projection["model_calls"]["unmetered"] == 1
    assert projection["usage_state"] == "partial"
