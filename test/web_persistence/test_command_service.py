from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from web_domain.errors import ConversationBusy, IdempotencyConflict
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def test_db_025_create_conversation_replay(db, account_id, database_url):
    service = CommandService(database_url)
    key = str(uuid4())
    one = service.create_conversation(account_id, key, "one")
    two = service.create_conversation(account_id, key, "one")
    assert one == two
    assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 1


def test_db_006_key_with_different_body_conflicts(db, account_id, database_url):
    service = CommandService(database_url)
    key = str(uuid4())
    service.create_conversation(account_id, key, "one")
    with pytest.raises(IdempotencyConflict):
        service.create_conversation(account_id, key, "two")


def test_db_003_send_is_atomic_and_busy(db, account_id, database_url):
    service = CommandService(database_url)
    conversation = UUID(service.create_conversation(account_id, str(uuid4()))["conversation_id"])
    service.send_message(account_id, conversation, str(uuid4()), "hello")
    assert db.execute(
        "SELECT count(*) FROM runs WHERE conversation_id=%s AND status='queued'", (conversation,)
    ).fetchone()[0] == 1
    with pytest.raises(ConversationBusy):
        service.send_message(account_id, conversation, str(uuid4()), "again")
    assert db.execute("SELECT count(*) FROM messages").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM outbox_events").fetchone()[0] == 1


def test_send_message_atomically_binds_ready_input_file_to_message_and_run(
    db, account_id, database_url
):
    service = CommandService(database_url)
    conversation = UUID(service.create_conversation(account_id, str(uuid4()))["conversation_id"])
    file_id = uuid4()
    db.execute(
        """
        INSERT INTO stored_files(
          file_id, account_id, conversation_id, purpose, status,
          original_name, display_name, storage_key, content_type, encoding,
          size_bytes, sha256, ready_at, expires_at
        ) VALUES (
          %s, %s, %s, 'input', 'ready',
          'notes.txt', 'notes.txt', %s, 'text/plain', 'utf-8',
          5, %s, now(), now() + interval '1 hour'
        )
        """,
        (file_id, account_id, conversation, f"{account_id}/{file_id}", "a" * 64),
    )

    result = service.send_message(
        account_id,
        conversation,
        str(uuid4()),
        "analyse the attachment",
        file_ids=(file_id,),
    )
    run_id = UUID(result["run_id"])
    user_message_id = UUID(result["user_message"]["message_id"])

    assert db.execute(
        "SELECT role, ordinal FROM message_files WHERE message_id=%s AND file_id=%s",
        (user_message_id, file_id),
    ).fetchone() == ("input", 0)
    assert db.execute(
        "SELECT direction, logical_name FROM run_files WHERE run_id=%s AND file_id=%s",
        (run_id, file_id),
    ).fetchone() == ("input", "notes.txt")
    assert db.execute(
        "SELECT expires_at FROM stored_files WHERE file_id=%s", (file_id,)
    ).fetchone()[0] is None
