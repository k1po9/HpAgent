from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from conversation_domain.commands import CommandService
from resources.account_daily_budget import (
    AccountDailyBudgetConflict,
    AccountDailyBudgetExhausted,
    AccountDailyBudgetService,
    AccountModelEntitlementUnavailable,
)
from resources.model_budget_coordinator import ModelBudgetCoordinator
from resources.run_budget import RunBudgetConflict, RunBudgetError, RunBudgetService

pytestmark = pytest.mark.postgres


def _entitle(db, account_id, limit=100):
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,"
        "daily_token_limit,prompt_visibility) VALUES (%s,'standard',%s,'summary')",
        (account_id, limit),
    )


def _run(database_url, account_id):
    service = CommandService(database_url)
    conversation_id = service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    return service.send_message(account_id, conversation_id, str(uuid4()), "hello")["run_id"]


def test_reserve_replay_conflict_and_finite_exhaustion(
    db,
    account_id,
    worker_database_url,
):
    _entitle(db, account_id, 10)
    budget = AccountDailyBudgetService(worker_database_url)
    first = budget.reserve(account_id, "one", 7)
    assert first.replayed is False
    assert budget.reserve(account_id, "one", 7).replayed is True
    with pytest.raises(AccountDailyBudgetConflict):
        budget.reserve(account_id, "one", 6)
    with pytest.raises(AccountDailyBudgetExhausted):
        budget.reserve(account_id, "two", 4)
    assert db.execute(
        "SELECT used_tokens,reserved_tokens FROM account_daily_model_budgets WHERE account_id=%s",
        (account_id,),
    ).fetchone() == (0, 7)


def test_unlimited_usage_is_recorded_and_can_settle_estimated(
    db,
    account_id,
    worker_database_url,
):
    _entitle(db, account_id, None)
    budget = AccountDailyBudgetService(worker_database_url)
    reserved = budget.reserve(account_id, "large", 1_000_000)
    settled = budget.settle(
        account_id,
        "large",
        1_000_000,
        "estimated",
        quota_date=reserved.quota_date,
    )
    assert settled.state == "settled"
    assert db.execute(
        "SELECT used_tokens,reserved_tokens FROM account_daily_model_budgets WHERE account_id=%s",
        (account_id,),
    ).fetchone() == (1_000_000, 0)


def test_settle_and_release_move_only_the_expected_totals(
    db,
    account_id,
    worker_database_url,
):
    _entitle(db, account_id)
    budget = AccountDailyBudgetService(worker_database_url)
    first = budget.reserve(account_id, "settle", 20)
    second = budget.reserve(account_id, "release", 30)
    assert (
        budget.settle(
            account_id,
            "settle",
            12,
            "provider",
            quota_date=first.quota_date,
        ).replayed
        is False
    )
    assert (
        budget.settle(
            account_id,
            "settle",
            12,
            "provider",
            quota_date=first.quota_date,
        ).replayed
        is True
    )
    assert (
        budget.release(
            account_id,
            "release",
            quota_date=second.quota_date,
        ).state
        == "released"
    )
    assert db.execute(
        "SELECT used_tokens,reserved_tokens FROM account_daily_model_budgets WHERE account_id=%s",
        (account_id,),
    ).fetchone() == (12, 0)


def test_entitlement_limit_changes_apply_to_next_reservation(
    db,
    account_id,
    worker_database_url,
):
    _entitle(db, account_id, 20)
    budget = AccountDailyBudgetService(worker_database_url)
    budget.reserve(account_id, "one", 15)
    db.execute(
        "UPDATE account_entitlements SET daily_token_limit=10 WHERE account_id=%s",
        (account_id,),
    )
    with pytest.raises(AccountDailyBudgetExhausted):
        budget.reserve(account_id, "two", 1)
    db.execute(
        "UPDATE account_entitlements SET daily_token_limit=30 WHERE account_id=%s",
        (account_id,),
    )
    assert budget.reserve(account_id, "two", 1).state == "reserved"


def test_utc_rollover_creates_independent_daily_scopes(
    db,
    account_id,
    worker_database_url,
):
    _entitle(db, account_id, 10)
    budget = AccountDailyBudgetService(worker_database_url)
    before = datetime(2026, 9, 18, 23, 59, tzinfo=UTC)
    after = before + timedelta(minutes=2)
    budget.reserve(account_id, "same-operation", 10, at=before)
    budget.reserve(account_id, "same-operation", 10, at=after)
    assert db.execute(
        "SELECT quota_date,reserved_tokens FROM account_daily_model_budgets "
        "WHERE account_id=%s ORDER BY quota_date",
        (account_id,),
    ).fetchall() == [(before.date(), 10), (after.date(), 10)]


@pytest.mark.parametrize("state", ["disabled", "expired"])
def test_unavailable_entitlement_denies_new_reservation(
    db,
    account_id,
    worker_database_url,
    state,
):
    _entitle(db, account_id)
    if state == "disabled":
        db.execute("UPDATE accounts SET status='disabled' WHERE account_id=%s", (account_id,))
    else:
        db.execute(
            "UPDATE account_entitlements SET expires_at=now()-interval '1 second' "
            "WHERE account_id=%s",
            (account_id,),
        )
    with pytest.raises(AccountModelEntitlementUnavailable):
        AccountDailyBudgetService(worker_database_url).reserve(account_id, "denied", 1)


def test_coordinator_reserve_rolls_both_back_when_run_fails(
    db,
    account_id,
    worker_database_url,
):
    _entitle(db, account_id)
    coordinator = ModelBudgetCoordinator(worker_database_url)
    with pytest.raises(RunBudgetError, match="Run budget snapshot not found"):
        coordinator.reserve(
            account_id,
            uuid4(),
            "attempt",
            5,
            {"model_total_tokens": 5},
        )
    assert (
        db.execute(
            "SELECT count(*) FROM account_model_usage_ledger WHERE account_id=%s",
            (account_id,),
        ).fetchone()[0]
        == 0
    )


def test_coordinator_settle_rolls_both_back_when_run_fails(
    db,
    account_id,
    database_url,
    worker_database_url,
):
    _entitle(db, account_id)
    run_id = _run(database_url, account_id)
    coordinator = ModelBudgetCoordinator(worker_database_url)
    reservation = coordinator.reserve(
        account_id,
        run_id,
        "attempt",
        10,
        {"model_total_tokens": 10, "model_calls": 1},
    )
    with pytest.raises(RunBudgetConflict):
        coordinator.settle(
            account_id,
            run_id,
            "attempt",
            4,
            {"model_total_tokens": 4},
            "provider",
            quota_date=reservation.account.quota_date,
        )
    assert (
        db.execute(
            "SELECT state FROM account_model_usage_ledger WHERE account_id=%s",
            (account_id,),
        ).fetchone()[0]
        == "reserved"
    )


def test_coordinator_release_and_public_run_budget_regression(
    db,
    account_id,
    database_url,
    worker_database_url,
):
    _entitle(db, account_id)
    run_id = _run(database_url, account_id)
    coordinator = ModelBudgetCoordinator(worker_database_url)
    mutation = coordinator.reserve(
        account_id,
        run_id,
        "attempt",
        10,
        {"model_total_tokens": 10, "model_calls": 1},
    )
    released = coordinator.release(
        account_id,
        run_id,
        "attempt",
        quota_date=mutation.account.quota_date,
    )
    assert released.account.state == released.run.state == "released"

    run_budget = RunBudgetService(worker_database_url)
    run_budget.reserve(run_id, "public", {"tool_calls": 1})
    assert run_budget.settle(run_id, "public", {"tool_calls": 1}, "measured").state == "settled"
