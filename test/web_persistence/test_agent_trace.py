from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from agent_execution.tracing.repository import PostgresTraceRepository
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def test_trace_repository_builds_owned_tree(database_url, worker_database_url, account_id):
    commands = CommandService(database_url)
    conversation_id = UUID(
        commands.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    run_id = UUID(
        commands.send_message(account_id, conversation_id, str(uuid4()), "trace this")["run_id"]
    )
    repository = PostgresTraceRepository(worker_database_url)
    root_id, child_id = uuid4(), uuid4()

    trace_run = repository.create_trace_run(run_id, {"source": "test"})
    repository.start_event(run_id, root_id, None, "AgentExecution", "agent")
    repository.start_event(run_id, child_id, root_id, "LLMCall", "llm", {"model": "test"})
    repository.finish_event(run_id, child_id, "completed", {"tokens": 12})
    repository.finish_event(run_id, root_id, "completed")

    tree = repository.get_trace_tree(account_id, run_id)
    assert tree is not None
    assert tree.run.trace_run_id == trace_run.trace_run_id
    assert tree.run.status == "completed"
    assert tree.roots[0].event.name == "AgentExecution"
    assert tree.roots[0].children[0].event.name == "LLMCall"
    assert tree.roots[0].children[0].event.metadata == {
        "model": "test",
        "tokens": 12,
    }
    assert repository.get_trace_tree(uuid4(), run_id) is None
