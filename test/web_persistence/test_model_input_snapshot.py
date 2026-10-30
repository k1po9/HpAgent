from __future__ import annotations

from uuid import uuid4

import pytest

from persistence.uow import UnitOfWork
from resources.model_budget_coordinator import ModelBudgetCoordinator
from resources.model_client import ModelClient
from resources.model_input_snapshot import SnapshotConflict, SnapshotRepository

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def _prepared(content: str = "hello", *, endpoint: str = "chat:0"):
    return ModelClient({
        "api_key": "SNAPSHOT-SECRET-SENTINEL",
        "base_url": "https://example.test/v1", "model": "model-a",
        "endpoint_id": endpoint, "provider": "example", "api_format": "openai",
    }).prepare_request([{"role": "user", "content": content}])


def _reserve_snapshot(db, account_id, run_id, worker_database_url, repository, snapshot):
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard','none') ON CONFLICT DO NOTHING", (account_id,),
    )
    operation = repository.get(account_id, snapshot.snapshot_id)["operation_id"]
    coordinator = ModelBudgetCoordinator(worker_database_url)
    coordinator.reserve(
        account_id, run_id, operation, 8,
        {"model_input_tokens": 3, "model_output_tokens": 5, "model_total_tokens": 8, "model_calls": 1},
        snapshot_id=snapshot.snapshot_id,
    )
    return coordinator, operation


def test_snapshot_is_owner_scoped_secret_free_and_replay_safe(
    db, account_id, database_url, worker_database_url,
) -> None:
    _service, _conversation_id, run_id = _conversation_and_run(database_url, account_id)
    repository = SnapshotRepository(worker_database_url)
    model_call_id = uuid4()
    operation_id = f"model:{model_call_id}:a1:f1:test"
    values = dict(
        account_id=account_id, run_id=run_id, model_call_id=model_call_id,
        operation_id=operation_id, execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1,
    )
    first = repository.create(**values, prepared=_prepared())
    replay = repository.create(**values, prepared=_prepared())
    assert replay.snapshot_id == first.snapshot_id
    with pytest.raises(SnapshotConflict):
        repository.create(**values, prepared=_prepared("changed"))
    with UnitOfWork(worker_database_url) as uow:
        row = uow.execute(
            "SELECT provider_request_body::text AS body FROM model_input_snapshots "
            "WHERE snapshot_id=%s", (first.snapshot_id,),
        ).fetchone()
    assert "SNAPSHOT-SECRET-SENTINEL" not in row["body"]
    assert first.content_hash != repository.create(
        **{**values, "operation_id": f"model:{model_call_id}:a1:f2:url", "fallback_attempt": 2},
        prepared=ModelClient({
            "api_key": "SNAPSHOT-SECRET-SENTINEL", "base_url": "https://other.test/v1",
            "model": "model-a", "endpoint_id": "chat:0", "provider": "example",
            "api_format": "openai",
        }).prepare_request([{"role": "user", "content": "hello"}]),
    ).content_hash


def test_snapshot_binds_atomic_account_and_run_reservation(
    db, account_id, database_url, worker_database_url,
) -> None:
    _service, _conversation_id, run_id = _conversation_and_run(database_url, account_id)
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard','none')", (account_id,),
    )
    repository = SnapshotRepository(worker_database_url)
    model_call_id = uuid4()
    operation_id = f"model:{model_call_id}:a1:f1:test"
    snapshot = repository.create(
        account_id=account_id, run_id=run_id, model_call_id=model_call_id,
        operation_id=operation_id, execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1,
        prepared=_prepared(),
    )
    amounts = {
        "model_input_tokens": 3, "model_output_tokens": 5,
        "model_total_tokens": 8, "model_calls": 1,
    }
    mutation = ModelBudgetCoordinator(worker_database_url).reserve(
        account_id, run_id, operation_id, 8, amounts, snapshot_id=snapshot.snapshot_id,
    )
    assert mutation.account.state == mutation.run.state == "reserved"
    with UnitOfWork(worker_database_url) as uow:
        row = uow.execute(
            "SELECT snapshot_id FROM account_model_usage_ledger WHERE account_id=%s "
            "AND operation_id=%s", (account_id, operation_id),
        ).fetchone()
    assert row["snapshot_id"] == snapshot.snapshot_id


@pytest.mark.parametrize("revocation", ["expired_lease", "closed_execution", "cancelled_run"])
def test_dispatch_marker_rechecks_execution_authority(
    db, account_id, database_url, worker_database_url, revocation,
):
    from agent_activities.store import AgentDataStore
    from run_domain.execution import StaleFencingToken
    from run_domain.execution_context import fence_scope

    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url)
    lease = store.acquire_lease(str(account_id), str(run_id))
    repository = SnapshotRepository(worker_database_url)
    with fence_scope(str(account_id), str(run_id), lease.fencing_token, lease.execution_id):
        snapshot = repository.create(
            account_id=account_id, run_id=run_id, model_call_id=uuid4(),
            operation_id="model:revoked", execution_attempt=1, call_ordinal=1,
            fallback_attempt=1, phase="decision", entitlement_version=1, prepared=_prepared(),
        )
        if revocation == "expired_lease":
            db.execute(
                "UPDATE execution_attempt_leases SET lease_expires_at=now()-interval '1 second' "
                "WHERE execution_id=%s", (lease.execution_id,),
            )
        elif revocation == "closed_execution":
            db.execute("UPDATE run_executions SET status='failed' WHERE execution_id=%s",
                       (lease.execution_id,))
        else:
            db.execute("UPDATE runs SET status='cancelling' WHERE run_id=%s", (run_id,))
        with pytest.raises(StaleFencingToken):
            repository.mark_dispatched(account_id, snapshot.snapshot_id)
    assert db.execute(
        "SELECT count(*) FROM model_dispatches WHERE snapshot_id=%s", (snapshot.snapshot_id,),
    ).fetchone()[0] == 0


def test_dispatch_response_loss_cannot_authorize_a_second_send(
    db, account_id, database_url, worker_database_url,
):
    from resources.account_daily_budget import AccountDailyBudgetConflict

    _, _, run_id = _conversation_and_run(database_url, account_id)
    repository = SnapshotRepository(worker_database_url)
    snapshot = repository.create(
        account_id=account_id, run_id=run_id, model_call_id=uuid4(),
        operation_id="model:response-lost", execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1, prepared=_prepared(),
    )
    _reserve_snapshot(db, account_id, run_id, worker_database_url, repository, snapshot)
    repository.mark_dispatched(account_id, snapshot.snapshot_id)
    with pytest.raises(AccountDailyBudgetConflict):
        repository.mark_dispatched(account_id, snapshot.snapshot_id)
    assert db.execute(
        "SELECT count(*) FROM model_dispatches WHERE snapshot_id=%s", (snapshot.snapshot_id,),
    ).fetchone()[0] == 1


@pytest.mark.parametrize("dispatch_first", [False, True])
def test_dispatch_and_release_cannot_both_win_for_the_same_hold(
    db, account_id, database_url, worker_database_url, dispatch_first,
):
    from resources.account_daily_budget import AccountDailyBudgetConflict

    _, _, run_id = _conversation_and_run(database_url, account_id)
    repository = SnapshotRepository(worker_database_url)
    snapshot = repository.create(
        account_id=account_id, run_id=run_id, model_call_id=uuid4(),
        operation_id="model:dispatch-or-release", execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1, prepared=_prepared(),
    )
    coordinator, operation = _reserve_snapshot(
        db, account_id, run_id, worker_database_url, repository, snapshot,
    )
    if dispatch_first:
        repository.mark_dispatched(account_id, snapshot.snapshot_id)
        with pytest.raises(AccountDailyBudgetConflict):
            coordinator.release(account_id, run_id, operation)
    else:
        coordinator.release(account_id, run_id, operation)
        with pytest.raises(AccountDailyBudgetConflict):
            repository.mark_dispatched(account_id, snapshot.snapshot_id)
    assert db.execute(
        "SELECT state FROM account_model_usage_ledger WHERE account_id=%s AND operation_id=%s",
        (account_id, operation),
    ).fetchone()[0] == ("reserved" if dispatch_first else "released")


def test_snapshot_reservation_retry_across_utc_midnight_keeps_the_original_hold(
    db, account_id, database_url, worker_database_url,
):
    from datetime import UTC, datetime, timedelta

    _, _, run_id = _conversation_and_run(database_url, account_id)
    repository = SnapshotRepository(worker_database_url)
    snapshot = repository.create(
        account_id=account_id, run_id=run_id, model_call_id=uuid4(),
        operation_id="model:midnight", execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1, prepared=_prepared(),
    )
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard','none')", (account_id,),
    )
    coordinator = ModelBudgetCoordinator(worker_database_url)
    day = datetime.now(UTC).replace(hour=23, minute=59, second=59)
    amounts = {"model_input_tokens": 3, "model_output_tokens": 5, "model_total_tokens": 8, "model_calls": 1}
    original = coordinator.reserve(account_id, run_id, "model:midnight", 8, amounts,
                                   snapshot_id=snapshot.snapshot_id, at=day)
    replay = coordinator.reserve(account_id, run_id, "model:midnight", 8, amounts,
                                 snapshot_id=snapshot.snapshot_id, at=day + timedelta(seconds=2))
    assert replay.account.replayed and replay.run.replayed
    assert replay.account.quota_date == original.account.quota_date
    assert db.execute(
        "SELECT count(*) FROM account_model_usage_ledger WHERE account_id=%s", (account_id,),
    ).fetchone()[0] == 1
    repository.mark_dispatched(account_id, snapshot.snapshot_id)


def test_dispatch_rechecks_entitlement_after_preflight_and_reservation(
    db, account_id, database_url, worker_database_url,
):
    from resources.account_daily_budget import AccountModelEntitlementUnavailable

    _, _, run_id = _conversation_and_run(database_url, account_id)
    repository = SnapshotRepository(worker_database_url)
    snapshot = repository.create(
        account_id=account_id, run_id=run_id, model_call_id=uuid4(),
        operation_id="model:entitlement-changed", execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1, prepared=_prepared(),
    )
    _reserve_snapshot(db, account_id, run_id, worker_database_url, repository, snapshot)
    db.execute("UPDATE account_entitlements SET version=version+1 WHERE account_id=%s", (account_id,))
    with pytest.raises(AccountModelEntitlementUnavailable):
        repository.mark_dispatched(account_id, snapshot.snapshot_id)
    assert db.execute(
        "SELECT count(*) FROM model_dispatches WHERE snapshot_id=%s", (snapshot.snapshot_id,),
    ).fetchone()[0] == 0


def test_api_terminal_without_usage_does_not_require_worker_budget_privileges(
    db, account_id, database_url,
):
    from run_domain.lifecycle import RunLifecycleService

    _, _, run_id = _conversation_and_run(database_url, account_id)
    assert not db.execute(
        "SELECT has_table_privilege('hpagent_api','hpagent.run_budgets','UPDATE')",
    ).fetchone()[0]
    assert RunLifecycleService(database_url).finish(
        account_id, run_id, "failed", failure_code="not_dispatched",
    )


@pytest.mark.parametrize("dispatched", [False, True])
def test_terminal_run_recovers_orphaned_model_holds_atomically(
    db, account_id, database_url, worker_database_url, dispatched,
):
    from run_domain.lifecycle import RunLifecycleService

    _, _, run_id = _conversation_and_run(database_url, account_id)
    db.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard','none')", (account_id,),
    )
    repository = SnapshotRepository(worker_database_url)
    snapshot = repository.create(
        account_id=account_id, run_id=run_id, model_call_id=uuid4(),
        operation_id="model:orphaned", execution_attempt=1, call_ordinal=1,
        fallback_attempt=1, phase="decision", entitlement_version=1, prepared=_prepared(),
    )
    ModelBudgetCoordinator(worker_database_url).reserve(
        account_id, run_id, "model:orphaned", 8,
        {"model_input_tokens": 3, "model_output_tokens": 5, "model_total_tokens": 8, "model_calls": 1},
        snapshot_id=snapshot.snapshot_id,
    )
    if dispatched:
        repository.mark_dispatched(account_id, snapshot.snapshot_id)
    lifecycle = RunLifecycleService(worker_database_url)
    lifecycle.finish(account_id, run_id, "failed", failure_code="worker_lost")
    lifecycle.finish(account_id, run_id, "failed", failure_code="worker_lost")
    expected = "settled" if dispatched else "released"
    assert db.execute(
        "SELECT state,actual_tokens,usage_source FROM account_model_usage_ledger "
        "WHERE account_id=%s AND operation_id='model:orphaned'", (account_id,),
    ).fetchone() == (expected, 8 if dispatched else None, "estimated" if dispatched else None)
    assert db.execute(
        "SELECT DISTINCT state FROM run_usage_ledger WHERE run_id=%s "
        "AND operation_id='model:orphaned'", (run_id,),
    ).fetchall() == [(expected,)]
    assert db.execute(
        "SELECT used_tokens,reserved_tokens FROM account_daily_model_budgets WHERE account_id=%s",
        (account_id,),
    ).fetchone() == (8 if dispatched else 0, 0)
