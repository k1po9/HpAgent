from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from agent_activities.fencing import fence_scope
from agent_activities.store import (
    AgentDataStore,
    LeaseConflict,
    RunNotExecutable,
    SegmentClosed,
    StaleFencingToken,
)
from agent_workflows.lifecycle_contracts import FinishWaitInput, SegmentInput, WaitInput

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def segment(account_id, run_id):
    return SegmentInput(1, str(run_id), str(account_id), str(uuid4()))


def wait_input(account_id, run_id):
    return WaitInput(
        1,
        str(run_id),
        str(account_id),
        str(uuid4()),
        "stable-operation",
        "external_callback",
        "callback:1",
        (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
    )


def test_segment_expiry_reacquire_rejects_late_writes_and_cleanup(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=2)
    first = segment(account_id, run_id)
    token = store.acquire_segment(first)
    assert store.acquire_segment(first) == token  # lost acquire response
    with pytest.raises(LeaseConflict):
        store.acquire_segment(segment(account_id, run_id))
    with fence_scope(str(account_id), str(run_id), token):
        store.begin_operation("stable-operation", str(run_id), "model")
    db.execute(
        "UPDATE account_execution_leases SET lease_expires_at=now()-interval '1 second' WHERE account_id=%s",
        (account_id,),
    )
    current = store.acquire_segment(first)
    assert current > token
    with fence_scope(str(account_id), str(run_id), token), pytest.raises(StaleFencingToken):
        store.complete_operation("stable-operation", "late-result", {"late": True})
    assert not store.release_lease(str(account_id), str(run_id), token)
    store.release_segment(first)
    with pytest.raises(SegmentClosed):
        store.acquire_segment(first)  # released acquire cannot resurrect
    second = segment(account_id, run_id)
    new_token = store.acquire_segment(second)
    assert new_token > current
    store.release_segment(first)  # delayed cleanup cannot release newer segment
    store.validate_and_renew_lease(str(account_id), str(run_id), new_token)
    with fence_scope(str(account_id), str(run_id), new_token):
        store.complete_operation("stable-operation", "valid-result", {"valid": True})
    store.release_segment(second)
    assert store.operation_result("stable-operation") == {"valid": True}


def test_wait_requires_release_and_run_remains_admitted_without_lease(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url)
    first, wait = segment(account_id, run_id), wait_input(account_id, run_id)
    token = store.acquire_segment(first)
    with pytest.raises(LeaseConflict):
        store.begin_wait(wait)
    store.release_segment(first)
    store.begin_wait(wait)
    with pytest.raises(LeaseConflict):
        store.acquire_segment(segment(account_id, run_id))
    store.begin_wait(wait)
    assert (
        db.execute(
            "SELECT count(*) FROM agent_run_waits WHERE wait_id=%s", (wait.wait_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT owner_run_id FROM account_execution_leases WHERE account_id=%s", (account_id,)
        ).fetchone()[0]
        is None
    )
    assert (
        db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == "queued"
    )
    assert store.finish_wait(FinishWaitInput(wait, "resumed"))
    resumed = segment(account_id, run_id)
    assert store.acquire_segment(resumed) > token
    store.release_segment(resumed)


def test_cancelled_run_cannot_resume_or_commit_with_previous_fence(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url)
    first = segment(account_id, run_id)
    token = store.acquire_segment(first)
    store.release_segment(first)
    wait = wait_input(account_id, run_id)
    store.begin_wait(wait)
    db.execute("UPDATE runs SET status='cancelling' WHERE run_id=%s", (run_id,))
    assert not store.finish_wait(FinishWaitInput(wait, "resumed"))
    assert (
        db.execute(
            "SELECT state FROM agent_run_waits WHERE wait_id=%s", (wait.wait_id,)
        ).fetchone()[0]
        == "cancelled"
    )
    with pytest.raises(RunNotExecutable):
        store.acquire_segment(segment(account_id, run_id))
    with pytest.raises(StaleFencingToken):
        store.validate_and_renew_lease(str(account_id), str(run_id), token)
    store.release_segment(first)


def test_release_before_acquire_delivery_is_a_permanent_segment_tombstone(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    store = AgentDataStore(worker_database_url)
    request = segment(account_id, run_id)
    store.release_segment(request)
    with pytest.raises(SegmentClosed):
        store.acquire_segment(request)
    with pytest.raises(RunNotExecutable):
        store.acquire_segment(replace(request, segment_id=str(uuid4()), account_id=str(uuid4())))
