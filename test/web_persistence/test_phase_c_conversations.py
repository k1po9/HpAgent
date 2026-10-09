"""Chat ownership is Conversation/Run/Execution based; Session is optional."""
from uuid import UUID, uuid4

import pytest

from agent_workflows.ids import root_execution_id
from conversation_domain.commands import CommandService
from web_domain.errors import ResourceNotFound

pytestmark = pytest.mark.postgres


@pytest.mark.parametrize("operation", ["send", "cancel", "retry"])
def test_foreign_account_cannot_mutate_conversation_or_run(
    db, account_id, database_url, operation
):
    commands = CommandService(database_url)
    conversation = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run = UUID(commands.send_message(account_id, conversation, str(uuid4()), "private")["run_id"])
    foreign = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (foreign,))
    before = db.execute("SELECT status,context_message_seq FROM runs WHERE run_id=%s", (run,)).fetchone()
    with pytest.raises(ResourceNotFound):
        if operation == "send":
            commands.send_message(foreign, conversation, str(uuid4()), "foreign")
        elif operation == "cancel":
            commands.cancel_run(foreign, run, str(uuid4()))
        else:
            commands.retry_run(foreign, run, str(uuid4()))
    assert db.execute("SELECT status,context_message_seq FROM runs WHERE run_id=%s", (run,)).fetchone() == before
    assert db.execute("SELECT count(*) FROM messages WHERE conversation_id=%s", (conversation,)).fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM idempotency_commands WHERE account_id=%s", (foreign,)).fetchone()[0] == 0


def test_sessionless_conversations_have_distinct_owned_root_executions(db, account_id, database_url):
    commands = CommandService(database_url)
    runs = []
    for _ in range(2):
        conversation = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
        run = UUID(commands.send_message(account_id, conversation, str(uuid4()), "private")["run_id"])
        runs.append(run)
        row = db.execute(
            "SELECT r.account_id,r.conversation_id,r.session_id,e.execution_id,e.role "
            "FROM runs r JOIN run_executions e ON e.run_id=r.run_id WHERE r.run_id=%s", (run,)
        ).fetchone()
        assert row == (account_id, conversation, None, root_execution_id(run), "root")
    assert root_execution_id(runs[0]) != root_execution_id(runs[1])
