from uuid import UUID, uuid4

import psycopg
import pytest

from agent_activities.fencing import fence_scope
from agent_activities.store import AgentDataStore, StaleFencingToken
from agent_workflows.lifecycle_contracts import SegmentInput
from conversation_domain.commands import CommandService
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import EffectService, ResultReceiptService
from web_api.sse import load_run_snapshot
from web_domain.errors import ConversationBusy
from web_domain.outbox import OutboxService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from work_domain.commands import WorkConflict
from work_domain.models import Requirement
from workspace.catalog import WorkspaceCatalog

pytestmark = pytest.mark.postgres


def mandate(commands, account_id):
    accepted = commands.accept(
        account_id,
        str(uuid4()),
        "Research",
        Requirement("Investigate", "research_report", {"schema_version": 1, "source_strategy": {}}),
    )
    work_id = UUID(accepted.body["work"]["work_id"])
    admitted = commands.advance(account_id, work_id, str(uuid4()), 1)
    return work_id, admitted.body["work"], UUID(admitted.body["run"]["run_id"])


def segment(store, account_id, run_id):
    identity = store.run_identity(str(run_id))
    return SegmentInput(
        2, str(run_id), str(account_id), str(uuid4()), execution_id=identity["execution_id"]
    )


def test_same_account_chat_and_work_have_independent_execution_contexts(
    owner, urls, commands, account_id
):
    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body["conversation_id"])
    first = chat.send_message(account_id, cid, str(uuid4()), "accept investigation")
    work_id, _, work_run = mandate(commands, account_id)
    RunLifecycleService(urls[2]).start(account_id, UUID(first["run_id"]))
    RunLifecycleService(urls[2]).finish(
        account_id, UUID(first["run_id"]), "succeeded", content="accepted"
    )
    second = chat.send_message(account_id, cid, str(uuid4()), "continue chatting")
    with pytest.raises(ConversationBusy):
        chat.send_message(account_id, cid, str(uuid4()), "same chat remains busy")
    other_cid = UUID(chat.create_conversation(account_id, str(uuid4())).body["conversation_id"])
    third = chat.send_message(account_id, other_cid, str(uuid4()), "new chat")
    store = AgentDataStore(urls[2])
    work_segment = segment(store, account_id, work_run)
    chat_segments = [segment(store, account_id, UUID(row["run_id"])) for row in (second, third)]
    work_token = store.acquire_segment(work_segment)
    for chat_segment in chat_segments:
        token = store.acquire_segment(chat_segment)
        with fence_scope(str(account_id), chat_segment.run_id, token, chat_segment.execution_id):
            operation = f"{chat_segment.execution_id}:context"
            store.begin_operation(operation, chat_segment.run_id, "context")
            store.create_transcript(
                transcript_id=f"t:{chat_segment.execution_id}",
                run_id=chat_segment.run_id,
                account_id=str(account_id),
                execution_id=chat_segment.execution_id,
                conversation_id=store.run_identity(chat_segment.run_id)["conversation_id"],
                messages=[{"role": "user", "content": "chat only"}],
                operation_id=operation,
            )
        store.release_segment(chat_segment)
    with fence_scope(str(account_id), str(work_run), work_token, work_segment.execution_id):
        with pytest.raises(StaleFencingToken):
            store.load_messages(f"t:{chat_segments[0].execution_id}")
    assert owner.execute(
        "SELECT conversation_id,session_id FROM runs WHERE run_id=%s", (work_run,)
    ).fetchone() == {"conversation_id": None, "session_id": None}
    assert (
        owner.execute(
            "SELECT count(*) n FROM messages WHERE produced_by_run_id=%s", (work_run,)
        ).fetchone()["n"]
        == 0
    )
    assert (
        owner.execute("SELECT to_regclass('hpagent.sessions') AS sessions").fetchone()["sessions"]
        is None
    )
    assert commands.get(account_id, work_id)["work"]["active_coordinator_run_id"] == str(work_run)
    work_snapshot = load_run_snapshot(urls[1], account_id, work_run)
    chat_snapshot = load_run_snapshot(urls[1], account_id, UUID(second["run_id"]))
    assert work_snapshot["source_kind"] == "work" and "assistant_message" not in work_snapshot
    assert (
        chat_snapshot["source_kind"] == "chat"
        and chat_snapshot["assistant_message"]["status"] == "pending"
    )
    with pytest.raises(psycopg.errors.RaiseException, match="outside its Execution"):
        owner.execute(
            "INSERT INTO agent_transcript_events(transcript_id,sequence,event_type,operation_id,payload) "
            "VALUES (%s,2,'system',%s,'{}'::jsonb)",
            (f"t:{chat_segments[0].execution_id}", f"{chat_segments[1].execution_id}:context"),
        )
    store.release_segment(work_segment)


@pytest.mark.parametrize("action", ["revise", "pause", "stop"])
def test_late_result_cannot_advance_current_work(owner, urls, commands, account_id, action):
    work_id, work, run_id = mandate(commands, account_id)
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, run_id)
    store = AgentDataStore(urls[2])
    request = segment(store, account_id, run_id)
    token = store.acquire_segment(request)
    operation_id = f"{request.execution_id}:model"
    with fence_scope(str(account_id), str(run_id), token, request.execution_id):
        store.begin_operation(operation_id, str(run_id), "model")
    if action == "revise":
        commands.revise(
            account_id,
            work_id,
            str(uuid4()),
            work["row_version"],
            Requirement(
                "New objective", "research_report", {"schema_version": 1, "source_strategy": {}}
            ),
        )
    else:
        commands.control(account_id, work_id, str(uuid4()), work["row_version"], action)
    with fence_scope(str(account_id), str(run_id), token, request.execution_id):
        with pytest.raises(StaleFencingToken):
            WorkspaceCatalog(urls[2]).initialize(account_id)
        with pytest.raises(StaleFencingToken):
            store.complete_operation(operation_id, "model-result", {"content": "old result"})
    row = owner.execute(
        "SELECT * FROM execution_result_receipts WHERE operation_id=%s", (operation_id,)
    ).fetchone()
    assert row["disposition"] == ("stale" if action == "revise" else "cancelled")
    assert row["requirement_revision"] == 1
    assert (
        ResultReceiptService(urls[2]).receive(
            str(account_id),
            str(run_id),
            request.execution_id,
            operation_id,
            token,
            {"content": "old result"},
        )
        == row["disposition"]
    )
    with pytest.raises(ValueError, match="conflict"):
        ResultReceiptService(urls[2]).receive(
            str(account_id),
            str(run_id),
            request.execution_id,
            operation_id,
            token,
            {"content": "different"},
        )
    lifecycle.finish(account_id, run_id, "cancelled")
    current = commands.get(account_id, work_id)["work"]
    assert current["active_coordinator_run_id"] is None
    assert current["status"] == {"revise": "active", "pause": "paused", "stop": "stopped"}[action]
    assert current["completed_requirement_revision"] is None


def test_unknown_external_write_prevents_stop_and_readmission(owner, urls, commands, account_id):
    work_id, work, run_id = mandate(commands, account_id)
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, run_id)
    store = AgentDataStore(urls[2])
    request = segment(store, account_id, run_id)
    token = store.acquire_segment(request)
    operation_id = f"{request.execution_id}:write"
    with fence_scope(str(account_id), str(run_id), token, request.execution_id):
        store.begin_tool_operation(operation_id, str(run_id))
        store.record_operation_intent(operation_id, {"side_effect_class": "non_idempotent_write"})
    commands.control(account_id, work_id, str(uuid4()), work["row_version"], "stop")
    lifecycle.finish(account_id, run_id, "cancelled")
    lifecycle.converge_controls()
    assert commands.get(account_id, work_id)["work"]["status"] == "stopping"
    with pytest.raises(WorkConflict):
        commands.advance(
            account_id,
            work_id,
            str(uuid4()),
            commands.get(account_id, work_id)["work"]["row_version"],
        )
    ResultReceiptService(urls[2]).receive(
        str(account_id),
        str(run_id),
        request.execution_id,
        operation_id,
        token,
        {"tool_success": True, "result": "provider receipt"},
    )
    lifecycle.converge_controls()
    assert commands.get(account_id, work_id)["work"]["status"] == "stopped"


def test_effect_key_reuses_provenance_and_rejects_parameter_conflict(
    owner, urls, commands, account_id
):
    work_id, _, run_id = mandate(commands, account_id)
    store = AgentDataStore(urls[2])
    request = segment(store, account_id, run_id)
    effects = EffectService(urls[2])
    first = effects.register(
        str(account_id),
        str(run_id),
        request.execution_id,
        f"{request.execution_id}:effect",
        "report:1:delivery",
        {"target": "private"},
    )
    RunLifecycleService(urls[2]).finish(account_id, run_id, "failed", failure_code="test_failure")
    current = commands.get(account_id, work_id)["work"]
    retry = commands.advance(account_id, work_id, str(uuid4()), current["row_version"])
    retry_id = retry.body["run"]["run_id"]
    execution = store.run_identity(retry_id)["execution_id"]
    reused = effects.register(
        str(account_id),
        retry_id,
        execution,
        f"{execution}:effect",
        "report:1:delivery",
        {"target": "private"},
    )
    assert reused["operation_id"] == first["operation_id"] and reused["run_id"] == run_id
    with pytest.raises(ValueError, match="parameter conflict"):
        effects.register(
            str(account_id),
            retry_id,
            execution,
            f"{execution}:effect",
            "report:1:delivery",
            {"target": "group"},
        )
    row = owner.execute(
        "SELECT * FROM execution_effect_references WHERE execution_id=%s", (UUID(execution),)
    ).fetchone()
    assert row["producing_run_id"] == run_id
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(urls[1]) as api:
            api.execute("UPDATE hpagent.execution_operations SET status='failed'")


def test_dispatch_dead_letter_uses_work_lifecycle(owner, urls, commands, account_id):
    work_id, _, run_id = mandate(commands, account_id)
    store = PostgresWorkflowExecutionStore(urls[2])
    decision = store.prepare_start(run_id)
    assert decision.should_start
    assert store.prepare_start(run_id) == decision
    outbox = OutboxService(urls[2])
    for event in outbox.claim("phase2", {"start_run"}, 100):
        outbox.dead_letter(event["outbox_event_id"], "phase2", "test_exhausted", "dispatch failed")
    assert commands.get(account_id, work_id)["work"]["active_coordinator_run_id"] is None
    assert (
        owner.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()["status"]
        == "failed"
    )
    assert (
        owner.execute(
            "SELECT execution_id FROM workflow_executions WHERE run_id=%s", (run_id,)
        ).fetchone()["execution_id"]
        is not None
    )


@pytest.mark.asyncio
async def test_expired_attempt_cleanup_preserves_new_scratch_without_git(
    owner, urls, commands, account_id, tmp_path
):
    from contextlib import AsyncExitStack
    from pathlib import Path

    from workspace.execution import ExecutionResourceService

    _, _, run_id = mandate(commands, account_id)
    RunLifecycleService(urls[2]).start(account_id, run_id)
    store = AgentDataStore(urls[2])

    class Sandbox:
        def __init__(self):
            self.active = {}

        def create_execution_sandbox(self, execution_id, workspace_path, **kwargs):
            self.active[execution_id] = Path(workspace_path)
            return execution_id

        def destroy_sandbox(self, sandbox_id):
            self.active.pop(sandbox_id)

    sandbox = Sandbox()
    resources = ExecutionResourceService(urls[2], sandbox, execution_root=tmp_path)
    old = segment(store, account_id, run_id)
    first = store.acquire_segment(old)
    async with AsyncExitStack() as previous:
        with fence_scope(str(account_id), str(run_id), first, old.execution_id):
            await previous.enter_async_context(resources.lease_for_run(account_id, run_id))
        old_path = sandbox.active[f"{old.execution_id}:{first}"]
        owner.execute(
            "UPDATE execution_attempt_leases SET lease_expires_at=now()-interval '1 second' WHERE execution_id=%s",
            (UUID(old.execution_id),),
        )
        replacement = segment(store, account_id, run_id)
        second = store.acquire_segment(replacement)
        async with AsyncExitStack() as current:
            with fence_scope(str(account_id), str(run_id), second, replacement.execution_id):
                await current.enter_async_context(resources.lease_for_run(account_id, run_id))
            new_path = sandbox.active[f"{replacement.execution_id}:{second}"]
            (new_path / "result.txt").write_text("current")
            assert old_path != new_path
            await previous.aclose()
            store.release_segment(old)
            assert not old_path.exists() and (new_path / "result.txt").read_text() == "current"
            assert f"{replacement.execution_id}:{second}" in sandbox.active
            assert (
                store.validate_and_renew_lease(str(account_id), str(run_id), second).fencing_token
                == second
            )
            assert not list(tmp_path.rglob(".git"))
        store.release_segment(replacement)
    assert not sandbox.active
