from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from web_domain.outbox import OutboxService
from web_domain.services import CommandService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def _running(worker_database_url: str, run_id: UUID) -> None:
    with psycopg.connect(worker_database_url) as connection:
        connection.execute(
            "UPDATE hpagent.runs SET status='running',started_at=now(),updated_at=now() "
            "WHERE run_id=%s AND status='queued'",
            (run_id,),
        )


def _claim_start_in_subprocess(worker_database_url: str, run_id: str, marker_path: str) -> None:
    """A separate worker process claims the start_run Outbox lease, then hangs.

    Used to prove that a killed worker's lease is recovered and the same event
    can be reclaimed by a replacement worker — a real process fault, not a mock.
    """
    import json
    import time
    from pathlib import Path
    from uuid import UUID

    from web_domain.outbox import OutboxService

    event = OutboxService(worker_database_url).claim("crashed-worker", {"start_run"}, 1)[0]
    assert event["run_id"] == UUID(run_id)
    Path(marker_path).write_text(
        json.dumps({"outbox_event_id": str(event["outbox_event_id"])}),
        encoding="utf-8",
    )
    while True:
        time.sleep(1)


def test_db_008_concurrent_retry_returns_one_child(
    db, account_id, database_url, worker_database_url, migration_database_url
):
    _, _, source_id = _conversation_and_run(database_url, account_id)
    CommandService(worker_database_url).fail_run(account_id, source_id, "test_failure")
    service = CommandService(database_url)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: service.retry_run(account_id, source_id, str(uuid4())), range(2)
            )
        )
    assert dict(results[0]) == dict(results[1])
    assert sorted(result.response_status for result in results) == [200, 202]
    child_id = UUID(results[0]["run_id"])
    with psycopg.connect(migration_database_url, autocommit=True) as verification:
        assert verification.execute(
            "SELECT count(*) FROM hpagent.runs WHERE retry_of_run_id=%s AND run_id=%s",
            (source_id, child_id),
        ).fetchone()[0] == 1
        statuses = verification.execute(
            "SELECT array_agg(response_status ORDER BY response_status) "
            "FROM hpagent.idempotency_commands WHERE operation='retry_run'"
        ).fetchone()[0]
        assert statuses == [200, 202]


def test_db_010_terminal_failure_before_outbox_rolls_back(
    db, account_id, database_url, migration_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    with pytest.raises(RuntimeError), psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "UPDATE runs SET status='running',started_at=now() WHERE run_id=%s", (run_id,)
        )
        connection.execute(
            "UPDATE messages SET status='completed',content='x',completed_at=now() "
            "WHERE produced_by_run_id=%s",
            (run_id,),
        )
        connection.execute(
            "UPDATE runs SET status='completed',finished_at=now() WHERE run_id=%s", (run_id,)
        )
        raise RuntimeError("injected before outbox")
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == "queued"
    assert db.execute(
        "SELECT status FROM messages WHERE produced_by_run_id=%s", (run_id,)
    ).fetchone()[0] == "pending"


def test_db_011_and_012_claim_crash_then_lease_recovery(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    service = OutboxService(worker_database_url)
    first = service.claim("worker-a", {"start_run"}, 1)[0]
    assert first["run_id"] == run_id
    assert service.recover_expired(0) == 1
    second = service.claim("worker-b", {"start_run"}, 1)[0]
    assert second["outbox_event_id"] == first["outbox_event_id"]
    assert second["attempt_count"] == 2
    assert service.mark_processed(second["outbox_event_id"], "worker-b")


def test_db_012_expired_lease_returns_to_pending(
    db, account_id, database_url, worker_database_url
):
    _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    event = outbox.claim("crashed-worker", {"start_run"}, 1)[0]
    assert outbox.recover_expired(0) == 1
    row = db.execute(
        "SELECT status,locked_at,locked_by FROM outbox_events WHERE outbox_event_id=%s",
        (event["outbox_event_id"],),
    ).fetchone()
    assert row == ("pending", None, None)


def test_db_013_late_completion_cannot_overwrite_failed(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    worker = CommandService(worker_database_url)
    assert worker.fail_run(account_id, run_id, "failed_first")
    assert not worker.complete_run(account_id, run_id, "late")
    assert db.execute(
        "SELECT status,failure_code FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone() == ("failed", "failed_first")


def test_db_019_cancel_queued_before_start_is_terminal(
    db, account_id, database_url, worker_database_url
):
    service, _, run_id = _conversation_and_run(database_url, account_id)
    result = service.cancel_run(account_id, run_id, str(uuid4()))
    assert result["status"] == "cancelled"
    event = OutboxService(worker_database_url).claim("dispatcher", {"start_run"}, 1)[0]
    assert event["event_type"] == "start_run"
    assert db.execute(
        "SELECT count(*) FROM workflow_executions WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == 0


def test_db_020_start_and_cancel_converge_without_duplicate_execution(
    db, account_id, database_url, worker_database_url
):
    service, conversation_id, run_id = _conversation_and_run(database_url, account_id)

    def schedule():
        with psycopg.connect(worker_database_url) as connection:
            connection.execute("SET search_path=hpagent,public")
            connection.execute(
                "SELECT 1 FROM conversations WHERE conversation_id=%s FOR UPDATE",
                (conversation_id,),
            )
            run = connection.execute(
                "SELECT workflow_id,status FROM runs WHERE run_id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if run[1] == "queued":
                connection.execute(
                    "INSERT INTO workflow_executions(workflow_execution_id,account_id,"
                    "conversation_id,run_id,workflow_id) VALUES (%s,%s,%s,%s,%s)",
                    (uuid4(), account_id, conversation_id, run_id, run[0]),
                )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(schedule), executor.submit(
            service.cancel_run, account_id, run_id, str(uuid4())
        )]
        [future.result() for future in futures]
    status = db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0]
    if status == "cancelling":
        with psycopg.connect(worker_database_url) as connection:
            connection.execute("SET search_path=hpagent,public")
            connection.execute(
                "UPDATE workflow_executions SET status='cancelled',closed_at=now() "
                "WHERE run_id=%s AND is_current",
                (run_id,),
            )
        assert CommandService(worker_database_url).cancelled_run(account_id, run_id)
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == "cancelled"
    assert db.execute(
        "SELECT count(*) FROM workflow_executions WHERE run_id=%s", (run_id,)
    ).fetchone()[0] <= 1


def test_db_021_start_dead_letter_converges_run(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    event = outbox.claim("dispatcher", {"start_run"}, 1)[0]
    assert outbox.dead_letter(
        event["outbox_event_id"], "dispatcher", "max_attempts", "start exhausted"
    )
    assert not outbox.mark_retryable_failure(
        event["outbox_event_id"], "dispatcher", "temporary", "safe",
        datetime.now(UTC) + timedelta(minutes=1),
    )
    assert db.execute(
        "SELECT status,failure_code FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone() == ("failed", "workflow_start_exhausted")
    assert db.execute(
        "SELECT count(*) FROM outbox_events WHERE business_key=%s",
        (f"terminal:{run_id}:failed",),
    ).fetchone()[0] == 1


def test_db_022_terminal_dead_letter_does_not_revert_completed(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    _running(worker_database_url, run_id)
    CommandService(worker_database_url).complete_run(account_id, run_id, "done")
    outbox = OutboxService(worker_database_url)
    event_id = outbox.claim("terminal-publisher", {"publish_terminal_event"}, 1)[0][
        "outbox_event_id"
    ]
    outbox.dead_letter(
        event_id, "terminal-publisher", "redis_down", "unavailable"
    )
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == "completed"


def test_terminal_outbox_carries_stable_event_id(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    _running(worker_database_url, run_id)
    CommandService(worker_database_url).complete_run(account_id, run_id, "done")
    row = db.execute(
        "SELECT outbox_event_id,payload->>'terminal_event_id' FROM outbox_events "
        "WHERE business_key=%s",
        (f"terminal:{run_id}:completed",),
    ).fetchone()
    assert str(row[0]) == row[1]


def test_db_024_expired_lease_recovery_keeps_single_outbox_and_deterministic_id(
    db, account_id, database_url, worker_database_url
):
    from orchestration.web_dispatcher import web_workflow_id

    _, _, run_id = _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)

    # Worker A claims the start_run Outbox (processing, attempt_count=1) and
    # crashes before marking it processed.
    claimed = outbox.claim("worker-a", {"start_run"}, 1)[0]
    assert claimed["event_type"] == "start_run"
    assert claimed["run_id"] == run_id
    assert claimed["attempt_count"] == 1
    assert web_workflow_id(claimed["run_id"]) == f"hpagent-web-run-{run_id}"

    # Simulate the lease expiring while Worker A is dead.
    with psycopg.connect(worker_database_url) as connection:
        connection.execute(
            "UPDATE outbox_events SET locked_at=now()-interval '120 seconds',"
            "updated_at=now() WHERE outbox_event_id=%s",
            (claimed["outbox_event_id"],),
        )

    # The production recovery sweep returns the expired lease to pending.
    assert outbox.recover_expired(60) == 1
    assert db.execute(
        "SELECT status,locked_at,locked_by FROM outbox_events WHERE outbox_event_id=%s",
        (claimed["outbox_event_id"],),
    ).fetchone() == ("pending", None, None)

    # Worker B reclaims the SAME event; attempt_count increments.
    recovered = outbox.claim("worker-b", {"start_run"}, 1)[0]
    assert recovered["outbox_event_id"] == claimed["outbox_event_id"]
    assert recovered["attempt_count"] == 2

    # Recovery never created a second start_run Outbox for the same Run.
    assert db.execute(
        "SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='start_run'",
        (run_id,),
    ).fetchone()[0] == 1

    # The deterministic Workflow ID is derived from the same Run ID both times.
    assert web_workflow_id(recovered["run_id"]) == web_workflow_id(claimed["run_id"])


def test_db_025_killed_worker_lease_recovered_by_replacement_dispatcher(
    db, account_id, database_url, worker_database_url, tmp_path
):
    import json
    import multiprocessing
    import time
    from uuid import UUID

    _, _, run_id = _conversation_and_run(database_url, account_id)
    marker = tmp_path / "claimed.json"
    process = multiprocessing.get_context("spawn").Process(
        target=_claim_start_in_subprocess,
        args=(worker_database_url, str(run_id), str(marker)),
    )
    process.start()
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker.exists(), "subprocess never claimed the start_run Outbox"
        event_id = UUID(json.loads(marker.read_text(encoding="utf-8"))["outbox_event_id"])

        # The worker process is killed without marking processed.
        process.kill()
        process.join(10)
        assert process.exitcode is not None

        # Simulate the lease expiring while the killed worker holds it.
        with psycopg.connect(worker_database_url) as connection:
            connection.execute(
                "UPDATE outbox_events SET locked_at=now()-interval '120 seconds',"
                "updated_at=now() WHERE outbox_event_id=%s",
                (event_id,),
            )

        # A replacement worker recovers and reclaims the SAME event.
        outbox = OutboxService(worker_database_url)
        assert outbox.recover_expired(60) == 1
        reclaimed = outbox.claim("replacement-worker", {"start_run"}, 1)[0]
        assert reclaimed["outbox_event_id"] == event_id
        assert reclaimed["attempt_count"] == 2
        assert db.execute(
            "SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='start_run'",
            (run_id,),
        ).fetchone()[0] == 1
    finally:
        if process.is_alive():
            process.kill()
            process.join(10)


def test_db_026_dispatcher_crash_after_claim_recovers_and_dispatches_once(
    db, account_id, database_url, worker_database_url
):
    from orchestration.web_dispatcher import (
        TemporalOutboxDispatcher,
        WebOutboxDispatcher,
        web_workflow_id,
    )
    from web_domain.workflow_execution import PostgresWorkflowExecutionStore

    _, _, run_id = _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)

    # Dispatcher A claims the start_run Outbox, then crashes before the Start RPC.
    claimed = outbox.claim("dispatcher-a", {"start_run"}, 1)[0]
    assert claimed["event_type"] == "start_run"
    assert claimed["run_id"] == run_id
    assert claimed["attempt_count"] == 1
    assert web_workflow_id(claimed["run_id"]) == f"hpagent-web-run-{run_id}"

    # The lease expires while Dispatcher A is dead.
    with psycopg.connect(worker_database_url) as connection:
        connection.execute(
            "UPDATE outbox_events SET locked_at=now()-interval '120 seconds',"
            "updated_at=now() WHERE outbox_event_id=%s",
            (claimed["outbox_event_id"],),
        )

    # Task-1 recovery returns the expired lease to pending.
    assert outbox.recover_expired(60) == 1

    class Temporal:
        def __init__(self):
            self.starts: list[tuple[str, str]] = []

        async def start_web_run(self, workflow_id, request):
            self.starts.append((workflow_id, str(request.run_id)))
            return "10000000-0000-0000-0000-000000000099"

        async def cancel_web_run(self, workflow_id):
            return False

    temporal = Temporal()
    store = PostgresWorkflowExecutionStore(worker_database_url)
    consumer = WebOutboxDispatcher(
        outbox,
        TemporalOutboxDispatcher(store, temporal),
        "dispatcher-b",
        max_attempts=3,
    )
    assert asyncio.run(consumer.run_once()) == 1

    # The replacement dispatcher started exactly one Workflow with the same
    # deterministic ID, and marked the reclaimed Outbox event processed.  No
    # second start_run event was created for the recovered Run.
    assert temporal.starts == [(web_workflow_id(run_id), str(run_id))]
    assert db.execute(
        "SELECT count(*) FROM workflow_executions WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT status FROM outbox_events WHERE outbox_event_id=%s",
        (claimed["outbox_event_id"],),
    ).fetchone()[0] == "processed"
    assert db.execute(
        "SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='start_run'",
        (run_id,),
    ).fetchone()[0] == 1


def test_db_027_lifecycle_txn_failure_rolls_back_atomically(
    db, account_id, database_url, worker_database_url
):
    from unittest.mock import patch

    _, _, run_id = _conversation_and_run(database_url, account_id)
    _running(worker_database_url, run_id)
    service = CommandService(worker_database_url)

    # A fault inside the authoritative lifecycle transaction (here, the terminal
    # message update) must roll the whole command back atomically.
    with patch.object(
        service.messages,
        "set_terminal",
        side_effect=RuntimeError("injected lifecycle txn failure"),
    ):
        with pytest.raises(RuntimeError, match="injected lifecycle txn failure"):
            service.fail_run(account_id, run_id, "txn_failure")

    assert db.execute(
        "SELECT status FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == "running"
    assert db.execute(
        "SELECT status FROM messages WHERE produced_by_run_id=%s", (run_id,)
    ).fetchone()[0] == "pending"
    # The setup's start_run event remains; the failed txn added no terminal one.
    assert db.execute(
        "SELECT count(*) FROM outbox_events WHERE run_id=%s AND event_type='publish_terminal_event'",
        (run_id,),
    ).fetchone()[0] == 0
