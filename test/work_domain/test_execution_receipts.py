"""Exact producing-attempt receipts, using only the caller's isolated PostgreSQL."""

from uuid import UUID, uuid4

import pytest
from support.work_fixtures import accept_research, advance_work

from agent_activities.store import AgentDataStore
from agent_workflows.lifecycle_contracts import SegmentInput
from persistence.uow import UnitOfWork
from run_domain.execution import StaleFencingToken
from run_domain.execution_context import fence_scope
from run_domain.results import ResultReceiptService, complete_operation

pytestmark = pytest.mark.postgres


def segment(store, account_id, run_id):
    return SegmentInput(
        2, str(run_id), str(account_id), str(uuid4()),
        execution_id=store.run_identity(str(run_id))["execution_id"],
    )


def producing_attempt(urls, commands, account_id):
    accepted = accept_research(commands, account_id, str(uuid4()), "Receipts", "Investigate")
    admitted = advance_work(commands, account_id, UUID(accepted.body["work"]["work_id"]), str(uuid4()))
    run_id = UUID(admitted.body["run"]["run_id"])
    store = AgentDataStore(urls[2])
    request = segment(store, account_id, run_id)
    token = store.acquire_segment(request)
    operation = f"{request.execution_id}:receipt"
    with fence_scope(str(account_id), str(run_id), token, request.execution_id):
        store.begin_operation(operation, str(run_id), "tool")
    return store, request, token, operation


def test_direct_completion_rechecks_the_producing_fence(owner, urls, commands, account_id):
    _, request, token, operation = producing_attempt(urls, commands, account_id)
    owner.execute(
        "UPDATE execution_attempt_leases SET lease_expires_at=now()-interval '1 second' "
        "WHERE execution_id=%s", (UUID(request.execution_id),),
    )
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        with pytest.raises(StaleFencingToken):
            with UnitOfWork(urls[2]) as uow:
                complete_operation(uow, operation, "result:old", {"content": "late"})
    assert owner.execute(
        "SELECT status FROM execution_operations WHERE operation_id=%s", (operation,),
    ).fetchone()["status"] == "started"


def test_completion_rejects_a_conflicting_registered_receipt(owner, urls, commands, account_id):
    store, request, token, operation = producing_attempt(urls, commands, account_id)
    receipts = ResultReceiptService(urls[2])
    assert receipts.receive(
        str(account_id), request.run_id, request.execution_id, operation, token,
        {"content": "original"},
    ) == "current"
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        with pytest.raises(ValueError, match="receipt payload conflict"):
            store.complete_operation(operation, "result:changed", {"content": "changed"})
        assert store.operation_result(operation) is None
        store.complete_operation(operation, "result:original", {"content": "original"})
        store.complete_operation(operation, "result:original", {"content": "original"})
    assert owner.execute(
        "SELECT count(*) n FROM execution_result_receipts WHERE operation_id=%s", (operation,),
    ).fetchone()["n"] == 1


def test_receipt_for_closed_execution_is_retained_as_stale(owner, urls, commands, account_id):
    _, request, token, operation = producing_attempt(urls, commands, account_id)
    owner.execute(
        "UPDATE run_executions SET status='failed' WHERE execution_id=%s",
        (UUID(request.execution_id),),
    )
    assert ResultReceiptService(urls[2]).receive(
        str(account_id), request.run_id, request.execution_id, operation, token,
        {"content": "late"},
    ) == "stale"


def test_legacy_reentry_counter_does_not_renumber_the_registered_producer(
    owner, urls, commands, account_id,
):
    store, request, token, operation = producing_attempt(urls, commands, account_id)
    # Older Activities counted redeliveries inside one fence as new attempts,
    # while their immutable producer row kept the first registered number.
    owner.execute(
        "UPDATE execution_operations SET attempt_count=2 WHERE operation_id=%s", (operation,),
    )
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        store.begin_operation(operation, request.run_id, "tool")
    assert ResultReceiptService(urls[2]).receive(
        str(account_id), request.run_id, request.execution_id, operation, token,
        {"content": "original producer"},
    ) == "current"
    assert owner.execute(
        "SELECT attempt_count FROM execution_operations WHERE operation_id=%s", (operation,),
    ).fetchone()["attempt_count"] == 2


@pytest.mark.parametrize("operation_type", ["tool", "model"])
def test_operation_reentry_keeps_the_registered_attempt(
    owner, urls, commands, account_id, operation_type,
):
    store, request, token, operation = producing_attempt(urls, commands, account_id)
    operation += ":" + operation_type
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        start = store.begin_tool_operation if operation_type == "tool" else (
            lambda op, run: store.begin_operation(op, run, "model")
        )
        start(operation, request.run_id)
        start(operation, request.run_id)
        store.complete_operation(operation, "result:once", {"content": "once"})
    row = owner.execute(
        "SELECT o.attempt_count,a.attempt_no FROM execution_operations o "
        "JOIN execution_operation_attempts a USING(operation_id) WHERE operation_id=%s",
        (operation,),
    ).fetchone()
    assert row == {"attempt_count": 1, "attempt_no": 1}


@pytest.mark.parametrize("uncertain", [False, True])
def test_generic_operation_entry_cannot_erase_an_unresolved_tool_intent(
    owner, urls, commands, account_id, uncertain,
):
    store, request, token, operation = producing_attempt(urls, commands, account_id)
    intent = {"side_effect_class": "non_idempotent_write", "idempotency_key": operation}
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        store.record_operation_intent(operation, intent)
        if uncertain:
            store.mark_operation_uncertain(operation, "response_lost")
        with pytest.raises(RuntimeError) as error:
            store.begin_operation(operation, request.run_id, "tool")
        assert error.value.code == "tool_side_effect_uncertain"
        state = store.begin_tool_operation(operation, request.run_id)
        assert state.status == ("uncertain" if uncertain else "intent_recorded")
        assert state.result_payload == intent


def test_replacement_attempt_retains_old_receipt_without_overwriting_result(
    owner, urls, commands, account_id,
):
    store, old, token, operation = producing_attempt(urls, commands, account_id)
    store.release_segment(old)
    current = segment(store, account_id, UUID(old.run_id))
    new_token = store.acquire_segment(current)
    assert new_token > token
    with fence_scope(str(account_id), current.run_id, new_token, current.execution_id):
        store.begin_operation(operation, current.run_id, "tool")
    assert ResultReceiptService(urls[2]).receive(
        str(account_id), old.run_id, old.execution_id, operation, token,
        {"content": "old"},
    ) == "stale"
    with fence_scope(str(account_id), current.run_id, new_token, current.execution_id):
        assert store.operation_result(operation) is None
        store.complete_operation(operation, "result:new", {"content": "new"})
        assert store.operation_result(operation) == {"content": "new"}


@pytest.mark.parametrize("dispatched", [False, True])
def test_work_model_hold_recovery_rolls_back_with_terminal_and_preserves_cumulative_usage(
    owner, urls, commands, account_id, monkeypatch, dispatched,
):
    from resources.model_budget_coordinator import ModelBudgetCoordinator
    from resources.model_client import ModelClient
    from resources.model_input_snapshot import SnapshotRepository
    from resources.run_budget import RunBudgetService
    from run_domain.lifecycle import RunLifecycleService

    _, request, token, operation = producing_attempt(urls, commands, account_id)
    run_id = UUID(request.run_id)
    owner.execute(
        "INSERT INTO account_entitlements(account_id,model_access_tier,prompt_visibility) "
        "VALUES (%s,'standard','none')", (account_id,),
    )
    prepared = ModelClient({"api_key": "fixture", "base_url": "https://example.test/v1",
                            "model": "fixture", "endpoint_id": "fixture"}).prepare_request([])
    repository = SnapshotRepository(urls[2])
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        snapshot = repository.create(
            account_id=account_id, run_id=run_id, model_call_id=uuid4(), operation_id=operation,
            execution_attempt=1, call_ordinal=1, fallback_attempt=1, phase="decision",
            entitlement_version=1, prepared=prepared,
        )
        ModelBudgetCoordinator(urls[2]).reserve(
            account_id, run_id, operation, 8,
            {"model_input_tokens": 3, "model_output_tokens": 5, "model_total_tokens": 8, "model_calls": 1},
            snapshot_id=snapshot.snapshot_id,
        )
        if dispatched:
            repository.mark_dispatched(account_id, snapshot.snapshot_id)

    def reject(*_args, **_kwargs):
        raise RuntimeError("injected budget failure")

    lifecycle = RunLifecycleService(urls[2])
    with monkeypatch.context() as patch:
        patch.setattr(RunBudgetService, "settle_in_uow" if dispatched else "release_in_uow", reject)
        with pytest.raises(RuntimeError, match="injected budget failure"):
            lifecycle.finish(account_id, run_id, "failed", failure_code="worker_lost")
    assert owner.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()["status"] == "queued"
    assert owner.execute(
        "SELECT state FROM account_model_usage_ledger WHERE account_id=%s AND operation_id=%s",
        (account_id, operation),
    ).fetchone()["state"] == "reserved"
    lifecycle.finish(account_id, run_id, "failed", failure_code="worker_lost")
    lifecycle.finish(account_id, run_id, "failed", failure_code="worker_lost")
    work = owner.execute(
        "SELECT w.status,w.completed_requirement_revision,b.used,b.reserved "
        "FROM works w JOIN work_budgets b USING(account_id,work_id) WHERE w.active_coordinator_run_id IS NULL",
    ).fetchone()
    assert work["status"] == "active" and work["completed_requirement_revision"] is None
    assert work["used"].get("model_total_tokens", 0) == (8 if dispatched else 0)
    assert work["reserved"].get("model_total_tokens", 0) == 0


def test_terminal_tool_hold_is_charged_without_resolving_unknown_external_effect(
    owner, urls, commands, account_id,
):
    from resources.run_budget import RunBudgetService
    from run_domain.lifecycle import RunLifecycleService

    store, request, token, operation = producing_attempt(urls, commands, account_id)
    run_id = UUID(request.run_id)
    run = owner.execute("SELECT work_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()
    work_id = run["work_id"]
    with fence_scope(str(account_id), request.run_id, token, request.execution_id):
        store.record_operation_intent(operation, {"side_effect_class": "non_idempotent_write"})
        RunBudgetService(urls[2]).reserve(run_id, operation, {"tool_calls": 1})
    work = commands.get(account_id, work_id)["work"]
    commands.control(account_id, work_id, str(uuid4()), work["row_version"], "stop")
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.finish(account_id, run_id, "cancelled")
    lifecycle.finish(account_id, run_id, "cancelled")
    assert owner.execute(
        "SELECT state,actual_amount,usage_source FROM run_usage_ledger "
        "WHERE run_id=%s AND operation_id=%s", (run_id, operation),
    ).fetchone() == {"state": "settled", "actual_amount": 1, "usage_source": "estimated"}
    assert owner.execute(
        "SELECT status FROM execution_operations WHERE operation_id=%s", (operation,),
    ).fetchone()["status"] == "intent_recorded"
    assert commands.get(account_id, work_id)["work"]["status"] == "stopping"
