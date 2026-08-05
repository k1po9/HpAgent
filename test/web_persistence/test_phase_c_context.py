from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from application.context_assembly import ContextAssemblyService, ContextIsolationError
from harness.context_builder import HarnessContextBuilder
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def _conversation(service: CommandService, account_id: UUID) -> UUID:
    return UUID(service.create_conversation(account_id, str(uuid4()))["conversation_id"])


def _complete(service: CommandService, account_id: UUID, run_id: UUID) -> None:
    assert service.start_run(account_id, run_id)
    assert service.complete_run(account_id, run_id, "completed reply")


def test_context_uses_only_frozen_visible_messages_of_its_conversation(
    db, account_id, database_url, worker_database_url
):
    commands = CommandService(database_url)
    first, other = _conversation(commands, account_id), _conversation(commands, account_id)
    first_run = UUID(commands.send_message(account_id, first, str(uuid4()), "first user")["run_id"])
    _complete(CommandService(worker_database_url), account_id, first_run)
    run_id = UUID(commands.send_message(account_id, first, str(uuid4()), "second user")["run_id"])
    commands.send_message(account_id, other, str(uuid4()), "other conversation")
    db.execute(
        "INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,sequence,"
        "client_request_id) VALUES (%s,%s,%s,'user','accepted','after watermark',5,%s)",
        (uuid4(), account_id, first, uuid4()),
    )

    base = ContextAssemblyService(database_url, HarnessContextBuilder()).load_base(account_id, run_id)

    assert [event.content.get("content", event.content.get("text")) for event in base.short_term_events] == [
        "first user", "completed reply", "second user"
    ]


def test_retry_context_reuses_the_original_watermark_without_duplicate_trigger(
    account_id, database_url, worker_database_url
):
    commands = CommandService(database_url)
    conversation = _conversation(commands, account_id)
    source = UUID(commands.send_message(account_id, conversation, str(uuid4()), "retry me")["run_id"])
    CommandService(worker_database_url).fail_run(account_id, source, "failed")
    retry = UUID(commands.retry_run(account_id, source, str(uuid4()))["run_id"])

    base = ContextAssemblyService(database_url, HarnessContextBuilder()).load_base(account_id, retry)

    assert base.context_message_seq == 1
    assert [event.content["content"] for event in base.short_term_events] == ["retry me"]


def test_context_refuses_cross_account_run_lookup(account_id, database_url):
    commands = CommandService(database_url)
    conversation = _conversation(commands, account_id)
    run_id = UUID(commands.send_message(account_id, conversation, str(uuid4()), "private")["run_id"])

    with pytest.raises(ContextIsolationError):
        ContextAssemblyService(database_url, HarnessContextBuilder()).load_base(uuid4(), run_id)
