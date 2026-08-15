from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from web_artifacts.services import ArtifactService
from web_domain.errors import IdempotencyConflict, ResourceNotFound
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def _completed_assistant(account_id, database_url, worker_database_url):
    chat = CommandService(database_url)
    conversation_id = UUID(chat.create_conversation(account_id, str(uuid4()))["conversation_id"])
    sent = chat.send_message(account_id, conversation_id, str(uuid4()), "source")
    run_id = UUID(sent["run_id"])
    CommandService(worker_database_url).complete_run(account_id, run_id, "# Dashboard\nA = 1")
    return chat, conversation_id, UUID(sent["assistant_message"]["message_id"])


def test_create_artifact_is_atomic_idempotent_and_does_not_block_chat(
    db, account_id, database_url, worker_database_url
):
    chat, conversation_id, message_id = _completed_assistant(
        account_id, database_url, worker_database_url
    )
    service, key = ArtifactService(database_url), str(uuid4())
    first = service.create_artifact(account_id, message_id, key)
    replay = service.create_artifact(account_id, message_id, key)
    assert first.body == replay.body
    assert replay.replayed is True
    assert db.execute("SELECT count(*) FROM artifacts").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM artifact_versions").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM artifact_outbox_events").fetchone()[0] == 1

    # Artifact Version remains queued, but the ordinary conversation lock is free.
    next_chat = chat.send_message(account_id, conversation_id, str(uuid4()), "continue")
    assert next_chat["run"]["status"] == "queued"


def test_artifact_rejects_cross_account_and_idempotency_payload_change(
    db, account_id, database_url, worker_database_url
):
    _, _, message_id = _completed_assistant(account_id, database_url, worker_database_url)
    service, key = ArtifactService(database_url), str(uuid4())
    service.create_artifact(account_id, message_id, key, "one")
    with pytest.raises(IdempotencyConflict):
        service.create_artifact(account_id, message_id, key, "two")
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(ResourceNotFound):
        service.create_artifact(other, message_id, str(uuid4()))


def test_user_or_pending_assistant_cannot_be_an_artifact_source(
    db, account_id, database_url
):
    chat = CommandService(database_url)
    conversation_id = UUID(chat.create_conversation(account_id, str(uuid4()))["conversation_id"])
    sent = chat.send_message(account_id, conversation_id, str(uuid4()), "source")
    service = ArtifactService(database_url)
    for field in ("user_message", "assistant_message"):
        with pytest.raises(ValueError, match="artifact_source_invalid"):
            service.create_artifact(
                account_id, UUID(sent[field]["message_id"]), str(uuid4())
            )
