from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock
from uuid import UUID, uuid4

import psycopg
import pytest

from persistence.repositories import RunRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import ConversationBusy
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def _conversation_and_run(database_url: str, account_id: UUID) -> tuple[CommandService, UUID, UUID]:
    service = CommandService(database_url)
    conversation_id = UUID(
        service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    run_id = UUID(
        service.send_message(account_id, conversation_id, str(uuid4()), "hello")["run_id"]
    )
    return service, conversation_id, run_id


def test_db_002_run_cannot_reference_other_conversation_message(db, account_id):
    other_account = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other_account,))
    own_conversation, other_conversation = uuid4(), uuid4()
    db.execute(
        "INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s),(%s,%s)",
        (own_conversation, account_id, other_conversation, other_account),
    )
    session_id, message_id = uuid4(), uuid4()
    db.execute(
        "INSERT INTO sessions(session_id,account_id,conversation_id,sequence) VALUES (%s,%s,%s,1)",
        (session_id, account_id, own_conversation),
    )
    db.execute(
        "INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,sequence,client_request_id) "
        "VALUES (%s,%s,%s,'user','accepted','x',1,%s)",
        (message_id, other_account, other_conversation, uuid4()),
    )
    run_id = uuid4()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute(
            "INSERT INTO runs(run_id,account_id,conversation_id,session_id,trigger_message_id,workflow_id,context_message_seq) "
            "VALUES (%s,%s,%s,%s,%s,%s,1)",
            (run_id, account_id, own_conversation, session_id, message_id, f"run-{run_id}"),
        )


def test_db_004_two_conversations_send_independently(db, account_id, database_url):
    service = CommandService(database_url)
    conversations = [
        UUID(service.create_conversation(account_id, str(uuid4()))["conversation_id"])
        for _ in range(2)
    ]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda conversation: service.send_message(
                    account_id, conversation, str(uuid4()), "hello"
                ),
                conversations,
            )
        )
    assert len(results) == 2
    assert db.execute(
        "SELECT array_agg(last_message_seq ORDER BY conversation_id) FROM conversations"
    ).fetchone()[0] == [2, 2]


def test_db_003_two_connections_compete_for_same_conversation(
    db, account_id, database_url
):
    service = CommandService(database_url)
    conversation_id = UUID(
        service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )

    def send(value: str):
        try:
            return service.send_message(account_id, conversation_id, str(uuid4()), value)
        except ConversationBusy:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(send, ("one", "two")))
    assert sum(result is not None for result in results) == 1
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2


def test_db_005_concurrent_same_key_replays_same_result(db, account_id, database_url):
    service = CommandService(database_url)
    conversation_id = UUID(
        service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: service.send_message(account_id, conversation_id, key, "same"),
                range(2),
            )
        )
    assert results[0] == results[1]
    assert db.execute("SELECT count(*) FROM outbox_events").fetchone()[0] == 1


def test_db_007_only_one_active_session(db, account_id):
    conversation_id = uuid4()
    db.execute(
        "INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s)",
        (conversation_id, account_id),
    )
    db.execute(
        "INSERT INTO sessions(session_id,account_id,conversation_id,sequence) VALUES (%s,%s,%s,1)",
        (uuid4(), account_id, conversation_id),
    )
    with pytest.raises(psycopg.errors.UniqueViolation):
        db.execute(
            "INSERT INTO sessions(session_id,account_id,conversation_id,sequence) VALUES (%s,%s,%s,2)",
            (uuid4(), account_id, conversation_id),
        )


def test_db_009_and_015_context_filters_status_and_account(
    db, account_id, database_url, worker_database_url
):
    _, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    with psycopg.connect(worker_database_url) as connection:
        connection.execute(
            "UPDATE hpagent.runs SET status='running',started_at=now() WHERE run_id=%s",
            (run_id,),
        )
    CommandService(worker_database_url).complete_run(account_id, run_id, "done")
    with UnitOfWork(database_url) as uow:
        visible = RunRepository().context_messages(uow, account_id, conversation_id, 2)
        hidden = RunRepository().context_messages(uow, uuid4(), conversation_id, 2)
    assert [row["status"] for row in visible] == ["accepted", "completed"]
    assert hidden == []


def test_run_timestamps_tolerate_a_small_system_clock_rollback(
    db, account_id, database_url
):
    service, _, run_id = _conversation_and_run(database_url, account_id)
    db.execute(
        "UPDATE runs SET created_at=clock_timestamp()+interval '2 seconds' WHERE run_id=%s",
        (run_id,),
    )
    db.execute(
        "UPDATE messages SET created_at=clock_timestamp()+interval '2 seconds' "
        "WHERE produced_by_run_id=%s",
        (run_id,),
    )

    service.start_run(account_id, run_id)
    service.complete_run(account_id, run_id, "done")

    run = db.execute(
        "SELECT created_at,started_at,finished_at FROM runs WHERE run_id=%s", (run_id,)
    ).fetchone()
    message = db.execute(
        "SELECT created_at,completed_at FROM messages WHERE produced_by_run_id=%s", (run_id,)
    ).fetchone()
    assert run[1] >= run[0] and run[2] >= run[1]
    assert message[1] >= message[0]


def test_db_015_cross_account_context_is_empty(db, account_id, database_url):
    _, conversation_id, _ = _conversation_and_run(database_url, account_id)
    with UnitOfWork(database_url) as uow:
        assert RunRepository().context_messages(uow, uuid4(), conversation_id, 100) == []


def test_db_016_retry_cannot_change_context_watermark(
    db, account_id, database_url, worker_database_url, migration_database_url
):
    _, conversation_id, source_id = _conversation_and_run(database_url, account_id)
    CommandService(worker_database_url).fail_run(account_id, source_id, "source_failed")
    source = db.execute(
        "SELECT session_id,trigger_message_id,context_message_seq FROM runs WHERE run_id=%s",
        (source_id,),
    ).fetchone()
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        sequence = connection.execute(
            "UPDATE conversations SET last_message_seq=last_message_seq+1 "
            "WHERE conversation_id=%s RETURNING last_message_seq",
            (conversation_id,),
        ).fetchone()[0]
        retry_id = uuid4()
        connection.execute(
            "INSERT INTO runs(run_id,account_id,conversation_id,session_id,trigger_message_id,"
            "retry_of_run_id,workflow_id,context_message_seq) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (retry_id, account_id, conversation_id, source[0], source[1], source_id,
             f"run-{retry_id}", source[2] + 1),
        )
        connection.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,sequence,produced_by_run_id) "
            "VALUES (%s,%s,%s,'assistant','pending',%s,%s)",
            (uuid4(), account_id, conversation_id, sequence, retry_id),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.commit()


def test_db_018_reverse_terminal_mismatch_is_rejected(
    db, account_id, database_url, migration_database_url
):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "UPDATE messages SET status='completed',content='bad',completed_at=now() "
            "WHERE produced_by_run_id=%s",
            (run_id,),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.commit()


def test_db_024_metadata_version_allows_one_writer(db, account_id):
    conversation_id = uuid4()
    db.execute(
        "INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s)",
        (conversation_id, account_id),
    )
    first = db.execute(
        "UPDATE conversations SET title='one',metadata_version=metadata_version+1 "
        "WHERE conversation_id=%s AND metadata_version=1 RETURNING metadata_version",
        (conversation_id,),
    ).fetchone()
    second = db.execute(
        "UPDATE conversations SET title='two',metadata_version=metadata_version+1 "
        "WHERE conversation_id=%s AND metadata_version=1 RETURNING metadata_version",
        (conversation_id,),
    ).fetchone()
    assert first == (2,)
    assert second is None


def test_serialization_failure_retries_the_entire_transaction(
    db, account_id, database_url
):
    service = CommandService(database_url)
    conversation_id = UUID(
        service.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    barrier = Barrier(2)
    counter_lock = Lock()
    invocation_count = 0

    @retryable_transaction
    def update_title(title: str) -> None:
        nonlocal invocation_count
        with counter_lock:
            invocation_count += 1
            synchronize = invocation_count <= 2
        with UnitOfWork(database_url) as uow:
            uow.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
            row = uow.execute(
                "SELECT metadata_version FROM conversations WHERE account_id=%s "
                "AND conversation_id=%s",
                (account_id, conversation_id),
            ).fetchone()
            if synchronize:
                barrier.wait(timeout=5)
            uow.execute(
                "UPDATE conversations SET title=%s,metadata_version=%s,updated_at=now() "
                "WHERE account_id=%s AND conversation_id=%s",
                (title, row["metadata_version"] + 1, account_id, conversation_id),
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(update_title, title) for title in ("one", "two")]
        [future.result() for future in futures]

    assert invocation_count >= 3
    assert db.execute(
        "SELECT metadata_version FROM conversations WHERE conversation_id=%s",
        (conversation_id,),
    ).fetchone()[0] == 3


def test_auth_session_rejects_qq_binding(db, account_id, migration_database_url):
    binding_id = uuid4()
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,normalized_subject_id,verified_at) "
        "VALUES (%s,%s,'qq','q','q',now())",
        (binding_id, account_id),
    )
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "INSERT INTO web_auth_sessions(web_auth_session_id,account_id,identity_binding_id,token_hash,csrf_secret_hash,expires_at,idle_expires_at) "
            "VALUES (%s,%s,%s,'a','b',now()+interval '1 day',now()+interval '1 hour')",
            (uuid4(), account_id, binding_id),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.commit()


def test_revoking_binding_with_live_auth_session_is_rejected(
    db, account_id, migration_database_url
):
    binding_id = uuid4()
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,normalized_subject_id,verified_at) "
        "VALUES (%s,%s,'web','web-user','web-user',now())",
        (binding_id, account_id),
    )
    db.execute(
        "INSERT INTO web_auth_sessions(web_auth_session_id,account_id,identity_binding_id,token_hash,csrf_secret_hash,expires_at,idle_expires_at) "
        "VALUES (%s,%s,%s,'token','csrf',now()+interval '1 day',now()+interval '1 hour')",
        (uuid4(), account_id, binding_id),
    )
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "UPDATE identity_bindings SET status='revoked',revoked_at=now() "
            "WHERE identity_binding_id=%s",
            (binding_id,),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.commit()


def test_session_predecessor_must_be_earlier(db, account_id, migration_database_url):
    conversation_id, earlier, later = uuid4(), uuid4(), uuid4()
    db.execute(
        "INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s)",
        (conversation_id, account_id),
    )
    db.execute(
        "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,status) VALUES (%s,%s,%s,2,'failed')",
        (later, account_id, conversation_id),
    )
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,status,predecessor_session_id) "
            "VALUES (%s,%s,%s,1,'failed',%s)",
            (earlier, account_id, conversation_id, later),
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.commit()


def test_changing_predecessor_sequence_cannot_invalidate_successor(
    db, account_id, migration_database_url
):
    conversation_id, predecessor, successor = uuid4(), uuid4(), uuid4()
    db.execute(
        "INSERT INTO conversations(conversation_id,account_id) VALUES (%s,%s)",
        (conversation_id, account_id),
    )
    db.execute(
        "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,status) "
        "VALUES (%s,%s,%s,1,'failed')",
        (predecessor, account_id, conversation_id),
    )
    db.execute(
        "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,status,predecessor_session_id) "
        "VALUES (%s,%s,%s,2,'failed',%s)",
        (successor, account_id, conversation_id, predecessor),
    )
    with psycopg.connect(migration_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute(
            "UPDATE sessions SET sequence=3 WHERE session_id=%s", (predecessor,)
        )
        with pytest.raises(psycopg.errors.RaiseException):
            connection.commit()
