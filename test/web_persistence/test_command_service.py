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
