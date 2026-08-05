from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
    assert results[0] == results[1]
    child_id = UUID(results[0]["run_id"])
    with psycopg.connect(migration_database_url, autocommit=True) as verification:
        assert verification.execute(
            "SELECT count(*) FROM hpagent.runs WHERE retry_of_run_id=%s AND run_id=%s",
            (source_id, child_id),
        ).fetchone()[0] == 1


def test_db_010_terminal_failure_before_outbox_rolls_back(
    db, account_id, database_url, migration_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    with pytest.raises(RuntimeError), psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
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
    first = service.claim("worker-a", 1)[0]
    assert first["run_id"] == run_id
    assert service.recover_expired(0) == 1
    second = service.claim("worker-b", 1)[0]
    assert second["outbox_event_id"] == first["outbox_event_id"]
    assert second["attempt_count"] == 2
    assert service.mark_processed(second["outbox_event_id"], "worker-b")


def test_db_012_expired_lease_returns_to_pending(
    db, account_id, database_url, worker_database_url
):
    _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    event = outbox.claim("crashed-worker", 1)[0]
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
    event = OutboxService(worker_database_url).claim("dispatcher", 1)[0]
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
    event = outbox.claim("dispatcher", 1)[0]
    outbox.dead_letter(event["outbox_event_id"], "max_attempts", "start exhausted")
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
    event_id = db.execute(
        "SELECT outbox_event_id FROM outbox_events WHERE business_key=%s",
        (f"terminal:{run_id}:completed",),
    ).fetchone()[0]
    OutboxService(worker_database_url).dead_letter(event_id, "redis_down", "unavailable")
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
