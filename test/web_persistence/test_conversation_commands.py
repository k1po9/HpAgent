"""W2-A: two callers share PG authority; this is not QQ ingress E2E."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest

from conversation_domain.admission import SingleActiveRunAdmission
from conversation_domain.commands import CommandService
from conversation_domain.sessions import ConversationSessionService
from web_domain.errors import ConversationBusy, IdempotencyConflict, ResourceNotFound

pytestmark = pytest.mark.postgres


def conversation(commands, account_id):
    return UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])


def test_surface_keys_replay_from_pg_across_command_instances(db, account_id, database_url):
    first, second = CommandService(database_url), CommandService(database_url)
    cid = conversation(first, account_id)
    key = "qq:napcat:bot-1:group:42:thread:7:message:123"
    accepted = first.send_message(account_id, cid, key, "hello")
    replay = second.send_message(account_id, cid, key, "hello")
    assert replay.replayed and replay.body == accepted.body
    assert "events_url" not in accepted.body
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 1
    assert db.execute("SELECT event_type FROM outbox_events").fetchall() == [("start_run",)]
    with pytest.raises(IdempotencyConflict):
        second.send_message(account_id, cid, key, "changed")
    # Retained Message identity survives expiry/removal of the response cache.
    db.execute("DELETE FROM idempotency_commands WHERE operation='send_message'")
    with pytest.raises(IdempotencyConflict):
        second.send_message(account_id, cid, key, "hello", agent_strategy="plan_and_execute")
    with pytest.raises(IdempotencyConflict):
        second.send_message(account_id, cid, key, "hello", file_ids=(uuid4(),))
    retained = second.send_message(account_id, cid, key, "hello")
    assert retained["run_id"] == accepted["run_id"]


def test_independent_surface_callers_race_at_one_pg_admission_slot(
    db, account_id, database_url,
):
    web, qq = CommandService(database_url), CommandService(database_url)
    cid = conversation(web, account_id)
    barrier = Barrier(2)

    def send(service, key):
        barrier.wait()
        try:
            return service.send_message(account_id, cid, key, "hello")["run_id"]
        except ConversationBusy:
            return "busy"

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(send, web, str(uuid4())), pool.submit(send, qq, "qq:bot:dm:1:msg:1")]
        results = [future.result() for future in futures]
    assert results.count("busy") == 1
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM outbox_events").fetchone()[0] == 1


def test_retained_replay_compares_file_identity_independent_of_storage_order(
    db, account_id, database_url,
):
    commands = CommandService(database_url)
    cid = conversation(commands, account_id)
    files = (uuid4(), uuid4())
    for file_id in files:
        db.execute(
            "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
            "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
            "ready_at,expires_at) VALUES (%s,%s,%s,'input','ready','notes.txt','notes.txt',"
            "%s,'text/plain','utf-8',5,%s,now(),now()+interval '1 hour')",
            (file_id, account_id, cid, f"{account_id}/{file_id}", "a" * 64),
        )
    request_files = tuple(reversed(files))
    accepted = commands.send_message(account_id, cid, "qq:bot:files:1", "read", file_ids=request_files)
    assert [item["file_id"] for item in accepted["user_message"]["files"]] == [str(f) for f in files]
    db.execute("DELETE FROM idempotency_commands WHERE operation='send_message'")
    replay = commands.send_message(account_id, cid, "qq:bot:files:1", "read", file_ids=request_files)
    assert replay["run_id"] == accepted["run_id"]


def test_same_account_keeps_distinct_conversations_and_enforces_ownership(
    db, account_id, database_url,
):
    commands = CommandService(database_url)
    private, group = conversation(commands, account_id), conversation(commands, account_id)
    one = commands.send_message(account_id, private, "qq:bot:dm:42:msg:1", "private")
    two = commands.send_message(account_id, group, "qq:bot:group:42:msg:1", "group")
    assert one["session_id"] != two["session_id"]
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(ResourceNotFound):
        commands.send_message(other, private, "qq:other:msg:1", "intrusion")
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == 2


def test_admission_policy_runs_in_command_transaction_for_send_and_retry(
    db, account_id, database_url, worker_database_url,
):
    class RecordingPolicy(SingleActiveRunAdmission):
        def __init__(self):
            self.transactions = []

        def admit_in_locked_conversation(self, uow, conversation_id):
            self.transactions.append(uow.execute("SELECT txid_current() AS id").fetchone()["id"])
            super().admit_in_locked_conversation(uow, conversation_id)

    policy = RecordingPolicy()
    commands = CommandService(database_url, admission_policy=policy)
    cid = conversation(commands, account_id)
    first = commands.send_message(account_id, cid, "qq:bot:msg:1", "hello")
    with pytest.raises(ConversationBusy):
        commands.send_message(account_id, cid, str(uuid4()), "busy")
    sessions = ConversationSessionService(database_url)
    with pytest.raises(ConversationBusy):
        sessions.rotate_active(account_id, cid)
    CommandService(worker_database_url).fail_run(account_id, UUID(first["run_id"]), "test_failure")
    successor = sessions.rotate_active(account_id, cid)
    retried = commands.retry_run(account_id, UUID(first["run_id"]), "qq:bot:retry:1")
    assert retried["run"]["session_id"] == str(successor)
    assert len(policy.transactions) == 3
    assert len(set(policy.transactions)) == 3
    assert db.execute("SELECT count(*) FROM runs WHERE status='queued'").fetchone()[0] == 1


def test_outbox_failure_rolls_back_admission_message_session_and_run(
    db, account_id, database_url, monkeypatch,
):
    commands = CommandService(database_url)
    cid = conversation(commands, account_id)

    def fail(*args, **kwargs):
        raise RuntimeError("outbox unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(commands.outbox, "enqueue", fail)
        with pytest.raises(RuntimeError, match="outbox unavailable"):
            commands.send_message(account_id, cid, "qq:bot:msg:1", "hello")
    for table in ("messages", "sessions", "runs", "outbox_events", "run_budgets"):
        assert db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    assert db.execute("SELECT last_message_seq FROM conversations").fetchone()[0] == 0
    assert commands.send_message(account_id, cid, "qq:bot:msg:1", "hello")["run_id"]
