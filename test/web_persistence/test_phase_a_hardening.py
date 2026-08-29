from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from persistence.migrate import migrate
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import OutboxLeaseLost
from web_domain.outbox import OutboxService
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


class _FakeConnection:
    def __init__(self, commit_error: Exception | None = None, cleanup_error: bool = False):
        self.commit_error = commit_error
        self.cleanup_error = cleanup_error
        self.closed = False
        self.rolled_back = False

    def execute(self, *_args, **_kwargs):
        return self

    def commit(self):
        if self.commit_error:
            raise self.commit_error

    def rollback(self):
        self.rolled_back = True
        if self.cleanup_error:
            raise RuntimeError("cleanup failure")

    def close(self):
        self.closed = True
        if self.cleanup_error:
            raise RuntimeError("close failure")


def _conversation_and_run(database_url: str, account_id: UUID):
    service = CommandService(database_url)
    conversation_id = UUID(
        service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    run_id = UUID(
        service.send_message(account_id, conversation_id, str(uuid4()), "hello")["run_id"]
    )
    return service, conversation_id, run_id


def _failed_source_and_retry(database_url: str, worker_database_url: str, account_id: UUID):
    service, conversation_id, source_id = _conversation_and_run(database_url, account_id)
    CommandService(worker_database_url).fail_run(account_id, source_id, "failed")
    child_id = UUID(service.retry_run(account_id, source_id, str(uuid4()))["run_id"])
    return conversation_id, source_id, child_id


def test_uow_closes_on_success_business_error_and_commit_error(monkeypatch):
    connections = [_FakeConnection(), _FakeConnection(cleanup_error=True), _FakeConnection(
        psycopg.errors.RaiseException("deferred failure")
    )]
    monkeypatch.setattr("persistence.uow.psycopg.connect", lambda *_a, **_k: connections.pop(0))

    successful = UnitOfWork("unused")
    with successful:
        pass
    assert successful.connection.closed

    failed = UnitOfWork("unused")
    with pytest.raises(ValueError, match="original"):
        with failed:
            raise ValueError("original")
    assert failed.connection.closed

    commit_failed = UnitOfWork("unused")
    with pytest.raises(psycopg.errors.RaiseException, match="deferred failure"):
        with commit_failed:
            pass
    assert commit_failed.connection.rolled_back
    assert commit_failed.connection.closed


def test_serialization_retry_closes_failed_connection(monkeypatch):
    created = [
        _FakeConnection(psycopg.errors.SerializationFailure("retry")),
        _FakeConnection(),
    ]
    queue = created.copy()
    monkeypatch.setattr("persistence.uow.psycopg.connect", lambda *_a, **_k: queue.pop(0))

    @retryable_transaction
    def operation():
        with UnitOfWork("unused"):
            pass

    operation()
    assert all(connection.closed for connection in created)


def test_concurrent_migration_is_locked_and_all_checksums_are_recorded(
    db, migration_database_url
):
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(migrate, (migration_database_url, migration_database_url)))
    assert db.execute(
        "SELECT count(*) FROM schema_migrations WHERE checksum IS NULL"
    ).fetchone()[0] == 0


def test_migration_checksum_rejects_changed_history(db, migration_database_url):
    version = "008_retry_reverse_and_terminal_transitions.sql"
    original = db.execute(
        "SELECT checksum FROM schema_migrations WHERE version=%s", (version,)
    ).fetchone()[0]
    db.execute(
        "UPDATE schema_migrations SET checksum='tampered' WHERE version=%s", (version,)
    )
    try:
        with pytest.raises(RuntimeError, match="migration checksum mismatch"):
            migrate(migration_database_url)
    finally:
        db.execute(
            "UPDATE schema_migrations SET checksum=%s WHERE version=%s", (original, version)
        )


def test_trigger_old_new_paths_support_legal_insert_update_delete(
    db, account_id, database_url, migration_database_url
):
    _, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    binding_id, auth_id = uuid4(), uuid4()
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
            "external_subject_id,normalized_subject_id,verified_at) "
            "VALUES (%s,%s,'web','subject','subject',now())",
            (binding_id, account_id),
        )
        connection.execute(
            "INSERT INTO web_auth_sessions(web_auth_session_id,account_id,identity_binding_id,"
            "token_hash,csrf_secret_hash,expires_at,idle_expires_at) "
            "VALUES (%s,%s,%s,'token','csrf',now()+interval '1 day',"
            "now()+interval '1 hour')",
            (auth_id, account_id, binding_id),
        )
        connection.execute(
            "UPDATE web_auth_sessions SET idle_expires_at=idle_expires_at+interval '1 minute' "
            "WHERE web_auth_session_id=%s",
            (auth_id,),
        )
        connection.execute(
            "UPDATE identity_bindings SET metadata='{\"updated\":true}'::jsonb "
            "WHERE identity_binding_id=%s",
            (binding_id,),
        )
        connection.execute(
            "DELETE FROM web_auth_sessions WHERE web_auth_session_id=%s", (auth_id,)
        )
        connection.execute(
            "DELETE FROM identity_bindings WHERE identity_binding_id=%s", (binding_id,)
        )

        disposable_session = uuid4()
        connection.execute(
            "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,status) "
            "VALUES (%s,%s,%s,2,'failed')",
            (disposable_session, account_id, conversation_id),
        )
        connection.execute(
            "UPDATE sessions SET summary='safe' WHERE session_id=%s", (disposable_session,)
        )
        connection.execute(
            "DELETE FROM sessions WHERE session_id=%s", (disposable_session,)
        )

        connection.execute("DELETE FROM outbox_events WHERE run_id=%s", (run_id,))
        connection.execute("DELETE FROM messages WHERE produced_by_run_id=%s", (run_id,))
        connection.execute("DELETE FROM run_usage_ledger WHERE run_id=%s", (run_id,))
        connection.execute("DELETE FROM run_budgets WHERE run_id=%s", (run_id,))
        connection.execute("DELETE FROM runs WHERE run_id=%s", (run_id,))
        connection.commit()


def test_retry_child_revalidates_source_status(
    db, account_id, database_url, worker_database_url, migration_database_url
):
    _, source_id, _ = _failed_source_and_retry(
        database_url, worker_database_url, account_id
    )
    with pytest.raises(psycopg.errors.RaiseException), psycopg.connect(
        migration_database_url
    ) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute("UPDATE runs SET status='running' WHERE run_id=%s", (source_id,))


def test_retry_child_revalidates_source_context(
    db, account_id, database_url, worker_database_url, migration_database_url
):
    _, source_id, _ = _failed_source_and_retry(
        database_url, worker_database_url, account_id
    )
    with pytest.raises(psycopg.errors.RaiseException), psycopg.connect(
        migration_database_url
    ) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "UPDATE runs SET context_message_seq=context_message_seq+1 WHERE run_id=%s",
            (source_id,),
        )
        connection.commit()


def test_retry_child_revalidates_source_trigger_change(
    db, account_id, database_url, worker_database_url, migration_database_url
):
    conversation_id, source_id, _ = _failed_source_and_retry(
        database_url, worker_database_url, account_id
    )
    replacement = uuid4()
    with pytest.raises(psycopg.errors.RaiseException), psycopg.connect(
        migration_database_url
    ) as connection:
        connection.execute("SET search_path=hpagent,public")
        sequence = connection.execute(
            "UPDATE conversations SET last_message_seq=last_message_seq+1 "
            "WHERE conversation_id=%s RETURNING last_message_seq",
            (conversation_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,"
            "sequence,client_request_id) VALUES (%s,%s,%s,'user','accepted','replacement',%s,%s)",
            (replacement, account_id, conversation_id, sequence, uuid4()),
        )
        connection.execute(
            "UPDATE runs SET trigger_message_id=%s,context_message_seq=%s WHERE run_id=%s",
            (replacement, sequence, source_id),
        )
        connection.commit()


def test_legal_failed_source_and_retry_commit(
    db, account_id, database_url, worker_database_url
):
    _, source_id, child_id = _failed_source_and_retry(
        database_url, worker_database_url, account_id
    )
    assert db.execute(
        "SELECT retry_of_run_id FROM runs WHERE run_id=%s", (child_id,)
    ).fetchone()[0] == source_id


@pytest.mark.parametrize(
    ("terminal_run", "terminal_message"),
    [("completed", "completed"), ("failed", "failed"), ("cancelled", "aborted")],
)
def test_terminal_run_and_message_cannot_revert(
    db, account_id, database_url, worker_database_url, migration_database_url,
    terminal_run, terminal_message
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    worker = CommandService(worker_database_url)
    if terminal_run == "completed":
        with psycopg.connect(worker_database_url) as connection:
            connection.execute(
                "UPDATE hpagent.runs SET status='running',started_at=now() WHERE run_id=%s",
                (run_id,),
            )
        worker.complete_run(account_id, run_id, "done")
    elif terminal_run == "failed":
        worker.fail_run(account_id, run_id, "failed")
    else:
        worker.cancelled_run(account_id, run_id)

    assert db.execute(
        "SELECT status FROM messages WHERE produced_by_run_id=%s", (run_id,)
    ).fetchone()[0] == terminal_message
    with pytest.raises(psycopg.errors.RaiseException), psycopg.connect(
        migration_database_url
    ) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute("UPDATE runs SET status='running' WHERE run_id=%s", (run_id,))
        connection.execute(
            "UPDATE messages SET status='pending' WHERE produced_by_run_id=%s", (run_id,)
        )


def test_failed_run_cannot_convert_to_completed(
    db, account_id, database_url, worker_database_url, migration_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    CommandService(worker_database_url).fail_run(account_id, run_id, "failed")
    with pytest.raises(psycopg.errors.RaiseException), psycopg.connect(
        migration_database_url
    ) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute("UPDATE runs SET status='completed' WHERE run_id=%s", (run_id,))


def test_outbox_dead_letter_requires_current_lease(
    db, account_id, database_url, worker_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    pending_id = db.execute(
        "SELECT outbox_event_id FROM outbox_events WHERE run_id=%s", (run_id,)
    ).fetchone()[0]
    with pytest.raises(OutboxLeaseLost):
        outbox.dead_letter(pending_id, "worker-a", "error", "safe")

    event = outbox.claim("worker-a", {"start_run"}, 1)[0]
    with pytest.raises(OutboxLeaseLost):
        outbox.dead_letter(event["outbox_event_id"], "worker-b", "error", "safe")
    assert outbox.mark_processed(event["outbox_event_id"], "worker-a")
    with pytest.raises(OutboxLeaseLost):
        outbox.dead_letter(event["outbox_event_id"], "worker-a", "error", "safe")


def test_expired_outbox_owner_cannot_dead_letter_after_reclaim(
    db, account_id, database_url, worker_database_url
):
    _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    event = outbox.claim("worker-a", {"start_run"}, 1)[0]
    assert outbox.recover_expired(0) == 1
    reclaimed = outbox.claim("worker-b", {"start_run"}, 1)[0]
    assert reclaimed["outbox_event_id"] == event["outbox_event_id"]
    with pytest.raises(OutboxLeaseLost):
        outbox.dead_letter(event["outbox_event_id"], "worker-a", "error", "safe")


def test_outbox_claim_filters_owned_types_and_does_not_block(
    db, account_id, database_url, worker_database_url
):
    _, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    with psycopg.connect(worker_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        for event_type in ("retain_memory", "publish_terminal_event"):
            connection.execute(
                "INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,"
                "conversation_id,run_id,payload) VALUES (%s,%s,%s,%s,%s,%s,'{}')",
                (uuid4(), account_id, event_type, f"test:{event_type}:{run_id}",
                 conversation_id, run_id),
            )
    outbox = OutboxService(worker_database_url)
    dispatcher = outbox.claim("dispatcher", {"start_run", "cancel_run"}, 10)
    memory = outbox.claim("memory", {"retain_memory"}, 10)
    terminal = outbox.claim("terminal", {"publish_terminal_event"}, 10)
    assert {row["event_type"] for row in dispatcher} == {"start_run"}
    assert {row["event_type"] for row in memory} == {"retain_memory"}
    assert {row["event_type"] for row in terminal} == {"publish_terminal_event"}


def test_skip_locked_claim_never_returns_same_event_twice(
    db, account_id, database_url, worker_database_url
):
    _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    with ThreadPoolExecutor(max_workers=2) as executor:
        batches = list(
            executor.map(
                lambda worker: outbox.claim(worker, {"start_run"}, 1),
                ("worker-a", "worker-b"),
            )
        )
    ids = [row["outbox_event_id"] for batch in batches for row in batch]
    assert len(ids) == len(set(ids)) == 1


def test_retryable_outbox_failure_honors_owner_and_schedule(
    db, account_id, database_url, worker_database_url
):
    _conversation_and_run(database_url, account_id)
    outbox = OutboxService(worker_database_url)
    event = outbox.claim("worker-a", {"start_run"}, 1)[0]
    future = datetime.now(UTC) + timedelta(minutes=5)
    assert not outbox.mark_retryable_failure(
        event["outbox_event_id"], "worker-b", "temporary", "safe", future
    )
    assert outbox.mark_retryable_failure(
        event["outbox_event_id"], "worker-a", "temporary", "safe", future
    )
    assert outbox.claim("worker-c", {"start_run"}, 1) == []
    db.execute(
        "UPDATE outbox_events SET available_at=now()-interval '1 second' "
        "WHERE outbox_event_id=%s",
        (event["outbox_event_id"],),
    )
    reclaimed = outbox.claim("worker-c", {"start_run"}, 1)[0]
    assert reclaimed["attempt_count"] == 2
    assert outbox.mark_processed(reclaimed["outbox_event_id"], "worker-c")
    assert not outbox.mark_retryable_failure(
        reclaimed["outbox_event_id"], "worker-c", "temporary", "safe", future
    )


def test_idempotency_freezes_http_status_and_full_send_result(
    db, account_id, database_url
):
    service = CommandService(database_url)
    create_key = str(uuid4())
    create_first = service.create_conversation(account_id, create_key)
    create_replay = service.create_conversation(account_id, create_key)
    assert create_replay == create_first
    assert create_first.response_status == create_replay.response_status == 201
    conversation_id = UUID(create_first["conversation_id"])
    send_key = str(uuid4())
    first = service.send_message(account_id, conversation_id, send_key, "hello")
    second = service.send_message(account_id, conversation_id, send_key, "hello")
    assert second == first
    assert first.response_status == second.response_status == 202
    assert first["user_message"]["status"] == "accepted"
    assert first["assistant_message"]["status"] == "pending"
    assert first["run"]["status"] == "queued"
    rows = db.execute(
        "SELECT operation,response_status,response_body FROM idempotency_commands "
        "WHERE idempotency_key IN (%s,%s) ORDER BY operation",
        (create_key, send_key),
    ).fetchall()
    assert {row[0]: row[1] for row in rows} == {
        "create_conversation": 201, "send_message": 202
    }
    assert db.execute("SELECT count(*) FROM outbox_events").fetchone()[0] == 1


def test_retry_freezes_complete_result(
    db, account_id, database_url, worker_database_url
):
    service, _, source_id = _conversation_and_run(database_url, account_id)
    CommandService(worker_database_url).fail_run(account_id, source_id, "failed")
    key = str(uuid4())
    first = service.retry_run(account_id, source_id, key)
    replay = service.retry_run(account_id, source_id, key)
    assert replay == first
    assert first.response_status == replay.response_status == 202
    assert first["source_run_id"] == str(source_id)
    assert first["assistant_message"]["status"] == "pending"
    assert first["run"]["status"] == "queued"
    assert db.execute(
        "SELECT response_status FROM idempotency_commands WHERE operation='retry_run' "
        "AND idempotency_key=%s",
        (key,),
    ).fetchone()[0] == 202
