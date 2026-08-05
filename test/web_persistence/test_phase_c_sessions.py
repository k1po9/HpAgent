from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from web_domain.errors import ConversationBusy
from web_domain.services import CommandService
from web_domain.sessions import ConversationSessionService

pytestmark = pytest.mark.postgres


def _conversation(service: CommandService, account_id: UUID) -> UUID:
    return UUID(service.create_conversation(account_id, str(uuid4()))["conversation_id"])


def test_web_sessions_are_unique_per_conversation_and_same_account_can_progress(
    db, account_id, database_url
):
    commands = CommandService(database_url)
    first, second = _conversation(commands, account_id), _conversation(commands, account_id)

    first_run = commands.send_message(account_id, first, str(uuid4()), "first")
    second_run = commands.send_message(account_id, second, str(uuid4()), "second")

    assert first_run["session_id"] != second_run["session_id"]
    assert db.execute(
        "SELECT count(*) FROM sessions WHERE account_id=%s AND status='active'", (account_id,)
    ).fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM sessions WHERE workspace_ref='account_repo'").fetchone()[0] == 2


def test_session_rotation_creates_a_successor_after_failed_session(db, account_id, database_url):
    commands = CommandService(database_url)
    conversation_id = _conversation(commands, account_id)
    sessions = ConversationSessionService(database_url)
    first = sessions.get_or_create_active(account_id, conversation_id)

    second = sessions.rotate_active(account_id, conversation_id, failed=True)

    assert second != first
    old = db.execute("SELECT status FROM sessions WHERE session_id=%s", (first,)).fetchone()
    new = db.execute(
        "SELECT status,predecessor_session_id FROM sessions WHERE session_id=%s", (second,)
    ).fetchone()
    assert old == ("failed",)
    assert new == ("active", first)


def test_session_rotation_refuses_to_replace_session_of_active_run(db, account_id, database_url):
    commands = CommandService(database_url)
    conversation_id = _conversation(commands, account_id)
    commands.send_message(account_id, conversation_id, str(uuid4()), "working")

    with pytest.raises(ConversationBusy):
        ConversationSessionService(database_url).rotate_active(account_id, conversation_id)
