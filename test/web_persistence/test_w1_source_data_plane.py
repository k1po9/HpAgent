"""Non-Chat contract fixture, not a new source or Research execution entrypoint."""
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from temporalio.exceptions import ApplicationError

from agent_activities.runtime import DurableAgentActivities
from agent_activities.store import AgentDataStore
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ContextBootstrapInput,
    ModelDecisionInput,
    RunContext,
    RunSource,
)
from agent_workflows.lifecycle_contracts import FinishWaitInput, SegmentInput, WaitInput
from brain.contracts import BrainDecision

pytestmark = pytest.mark.postgres


@pytest.fixture
def non_chat_run(db, account_id):
    # Use the schema's existing file_job shape without creating any Chat entities.
    task_id, run_id = uuid4(), uuid4()
    db.execute("INSERT INTO tasks(task_id,account_id,title,objective) VALUES (%s,%s,'fixture','fixture')", (task_id, account_id))
    db.execute("INSERT INTO runs(run_id,account_id,task_id,run_kind,workflow_id) VALUES (%s,%s,%s,'file_job',%s)",
               (run_id, account_id, task_id, f"contract-{run_id}"))
    return run_id, task_id


class SourceBindings:
    def validate_loaded(self, request, loaded):
        assert loaded.account_id == request.account_id
        assert request.context.chat is None
    def session_key(self, request):
        return request.context.context_ref
    def transcript_context(self, request):
        return {}  # No synthetic Conversation or Session.


class Resources:
    @asynccontextmanager
    async def lease_for_run(self, *args):
        yield


class Events:
    async def progress(self, *args): pass
    async def close(self): pass


class Actions:
    def reset_turn(self, key, run_id): assert key == "source:context"
    def clear_execution(self, key, run_id): assert key == "source:context"
    async def select_tools(self, **kwargs): return []


class Loader:
    def __init__(self, account_id): self.account_id = account_id
    async def load(self, run_id):
        return SimpleNamespace(account_id=str(self.account_id),
                               context=({"role": "user", "content": "fixture"},), context_provider=None)


class Brain:
    async def generate_chat_decision(self, **kwargs):
        return BrainDecision("source result", [], "stop", {})


@pytest.mark.asyncio
async def test_non_chat_bootstrap_wait_reacquire_and_model(db, account_id, worker_database_url, non_chat_run):
    run_id, source_id = non_chat_run
    store = AgentDataStore(worker_database_url)
    runtime = DurableAgentActivities(store=store, context_bindings=SourceBindings(), loader=Loader(account_id),
                                     brain=Brain(), actions=Actions(), event_factory=SimpleNamespace(for_run=lambda _: Events()),
                                     resource_prep=Resources(), lifecycle=None)
    first = SegmentInput(1, str(run_id), str(account_id), str(uuid4()))
    token = store.acquire_segment(first)
    request = ContextBootstrapInput(AGENT_SCHEMA_VERSION, str(run_id), str(account_id),
                                    RunSource("file_job", str(source_id)), RunContext(context_ref="source:context"),
                                    "react", f"{run_id}:context", lease_token=token)
    transcript = await runtime.context_bootstrap(request)
    assert db.execute("SELECT conversation_id,session_id FROM agent_transcripts WHERE run_id=%s", (run_id,)).fetchone() == (None, None)
    store.release_segment(first)
    wait = WaitInput(1, str(run_id), str(account_id), str(uuid4()), f"{run_id}:model", "model_review",
                     "review-reference", (datetime.now(UTC) + timedelta(minutes=1)).isoformat())
    store.begin_wait(wait)
    assert db.execute("SELECT owner_run_id FROM account_execution_leases WHERE account_id=%s", (account_id,)).fetchone() == (None,)
    assert store.finish_wait(FinishWaitInput(wait, "resumed"))
    second = replace(first, segment_id=str(uuid4()))
    current = store.acquire_segment(second)
    assert current > token
    model = ModelDecisionInput(AGENT_SCHEMA_VERSION, str(run_id), str(account_id), request.source, request.context,
                               "react", transcript.transcript_id, transcript.transcript_version, 1, f"{run_id}:model", current)
    with pytest.raises(ApplicationError) as stale:
        await runtime.model_decision(replace(model, lease_token=token))
    assert stale.value.type == "stale_fencing_token"
    result = await runtime.model_decision(model)
    assert store.result_content(result.decision_ref) == "source result"
    store.release_segment(second)
    assert db.execute("SELECT count(*) FROM conversations WHERE account_id=%s", (account_id,)).fetchone()[0] == 0


def test_null_chat_context_still_requires_run_account_ownership(db, account_id, worker_database_url, non_chat_run):
    run_id, _ = non_chat_run
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("INSERT INTO agent_transcripts(transcript_id,run_id,account_id) VALUES (%s,%s,%s)",
                   (str(uuid4()), run_id, other))
    store = AgentDataStore(worker_database_url)
    with pytest.raises(ValueError, match="Run ownership"):
        store.create_transcript(transcript_id=str(uuid4()), run_id=str(run_id), account_id=str(other),
                                messages=[], operation_id="invalid-owner")
