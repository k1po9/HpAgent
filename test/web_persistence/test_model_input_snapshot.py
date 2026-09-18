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
