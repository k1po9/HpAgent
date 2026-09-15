"""W1-C Web Command/Outbox -> canonical lifecycle -> real durable Activities."""

from __future__ import annotations

import asyncio
import os
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer

from agent.protocol import BrainDecision
from agent_activities.runtime import DurableAgentActivities
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_execution.chat_bindings import ChatExecutionBindings
from agent_execution.chat_run_input import ChatRunInputLoader
from agent_execution.web_adapters import PostgresWebRequestLoader
from agent_execution.web_events import RedisWebRunEventSinkFactory
from application.context_assembly import ContextAssemblyService
from conversation_domain.commands import CommandService
from harness.context_builder import HarnessContextBuilder
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_lifecycle_activities import (
    finalize_cancelled_activity,
    finalize_failed_activity,
    inject_agent_event_factory,
    inject_agent_run_loader,
    inject_run_lifecycle,
    load_agent_run_input_activity,
    prepare_run_activity,
)
from orchestration.run_lifecycle_contracts import RunLifecycleInput
from orchestration.web_dispatcher import (
    TemporalClientAdapter,
    TemporalOutboxDispatcher,
    WebOutboxDispatcher,
)
from orchestration.web_workers import build_web_temporal_workers
from sandbox.git_repo import GitRepoManager
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.outbox import OutboxService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from workspace.isolation import AccountLockRegistry, SessionResourceRecoveryService

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.temporal]


class Brain:
    def __init__(self, outcome):
        self.outcome = outcome
        self.entered = asyncio.Event()
        self.calls = 0

    async def rewrite_recall_query(self, **kwargs):
        return "hello", None

    async def generate_chat_decision(self, **kwargs):
        self.calls += 1
        self.entered.set()
        if self.outcome == "failed":
            raise ApplicationError(
                "model unavailable", type="model_unavailable", non_retryable=True
            )
        if self.outcome == "cancelled":
            await asyncio.Event().wait()
        return BrainDecision("canonical reply", [], "stop", {})

    async def generate_final_decision(self, *, messages):
        text = str(messages)
        if '"steps"' in text:
            return BrainDecision(
                '{"steps":[{"title":"answer","objective":"answer the question"}]}', [], "stop", {}
            )
        if '"decision"' in text:
            return BrainDecision('{"decision":"complete","reason":"done"}', [], "stop", {})
        return await self.generate_chat_decision(messages=messages)


class Actions:
    def reset_turn(self, *args):
        pass

    def clear_execution(self, *args):
        pass

    async def select_tools(self, **kwargs):
        return []


class Sandbox:
    def __init__(self):
        self.calls = []

    def create_session_sandbox(self, session_id, workspace_path, **kwargs):
        self.calls.append(str(session_id))
        return f"sandbox-{session_id}"


@pytest.mark.parametrize(
    "strategy,outcome,surface",
    [
        ("react", "completed", "web"),
        ("plan_and_execute", "completed", "web"),
        ("react", "failed", "web"),
        ("react", "cancelled", "web"),
        ("plan_and_execute", "failed", "web"),
        ("plan_and_execute", "cancelled", "web"),
        ("react", "completed", "napcat"),
        ("react", "cancelled", "napcat"),
    ],
)
async def test_web_canonical_lifecycle(
    db, account_id, database_url, worker_database_url, tmp_path, strategy, outcome, surface
):
    if not os.getenv("TEMPORAL_HOST"):
        pytest.skip("TEMPORAL_HOST required")
    command = CommandService(database_url)
    if surface == "napcat":
        from support.qq_messages import qq_message

        from application.conversation import ConversationService
        from application.ingress import MessageIngressService
        from conversation_domain.surface_commands import SurfaceConversationCommands
        db.execute(
            "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,external_subject_id,"
            "normalized_subject_id,verified_at) VALUES (%s,%s,'qq','123','napcat:123',now())",
            (uuid4(), account_id),
        )
        ingress = MessageIngressService(conversation_service=ConversationService(
            SurfaceConversationCommands(CommandService(worker_database_url))
        ))
        accepted = await ingress.handle(qq_message())
        conversation, run_id = UUID(accepted["conversation_id"]), UUID(accepted["run_id"])
    else:
        conversation = UUID(command.create_conversation(account_id, str(uuid4()))["conversation_id"])
        run_id = UUID(
            command.send_message(
                account_id, conversation, str(uuid4()), "hello", agent_strategy=strategy
            )["run_id"]
        )
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )
    lifecycle = WebRunLifecycleService(worker_database_url)
    store = AgentDataStore(worker_database_url)
    events = RedisWebRunEventSinkFactory(None)
    brain, sandbox = Brain(outcome), Sandbox()
    runtime = DurableAgentActivities(
        context_bindings=ChatExecutionBindings(),
        store=store,
        loader=PostgresWebRequestLoader(
            worker_database_url,
            ContextAssemblyService(worker_database_url, HarnessContextBuilder()),
        ),
        brain=brain,
        actions=Actions(),
        event_factory=events,
        lifecycle=lifecycle,
        resource_prep=SessionResourceRecoveryService(
            worker_database_url, sandbox, AccountLockRegistry(), GitRepoManager(tmp_path)
        ),
    )
    inject_run_lifecycle(lifecycle)
    inject_agent_run_loader(ChatRunInputLoader(store))
    inject_agent_event_factory(events)
    segments = SegmentActivities(store)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            prepare_run_activity,
            load_agent_run_input_activity,
            finalize_failed_activity,
            finalize_cancelled_activity,
            runtime.finalize_agent_result,
        ],
        agent_activities=[
            segments.acquire,
            segments.release,
            segments.begin_wait,
            segments.finish_wait,
            runtime.context_bootstrap,
            runtime.model_decision,
            runtime.tool_execution,
            runtime.planning,
            runtime.evaluate_plan,
        ],
    )
    executions = PostgresWorkflowExecutionStore(worker_database_url)
    adapter = TemporalClientAdapter(client)

    class LostAck:
        """Temporal accepts Start, but the first dispatcher loses its RPC response."""

        lost = False

        async def start_web_run(self, workflow_id, request):
            result = await adapter.start_web_run(workflow_id, request)
            if not self.lost:
                self.lost = True
                raise RuntimeError("lost start response")
            return result

        async def cancel_web_run(self, workflow_id):
            return await adapter.cancel_web_run(workflow_id)

    dispatch = TemporalOutboxDispatcher(executions, LostAck(), lifecycle)
    outbox = WebOutboxDispatcher(OutboxService(worker_database_url), dispatch, "w1c-test")
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(workers.lifecycle)
        await stack.enter_async_context(workers.agent)
        await outbox.run_once()
        handle = client.get_workflow_handle(f"hpagent-web-run-{run_id}")
        if outcome == "cancelled":
            await asyncio.wait_for(brain.entered.wait(), 30)
            if surface == "napcat":
                cancelled = await ingress.handle(qq_message("cancel-1", "/cancel"))
                assert cancelled["run_id"] == str(run_id)
            else:
                command.cancel_run(account_id, run_id, str(uuid4()))
            await outbox.run_once()
        if outcome == "completed":
            result = await asyncio.wait_for(handle.result(), 60)
            assert result["outcome"] == "completed"
        else:
            with pytest.raises(WorkflowFailureError):
                await asyncio.wait_for(handle.result(), 60)
        # Redelivery after the workflow already advanced/completed cannot execute twice.
        original_id = (await handle.describe()).run_id
        assert (
            await adapter.start_web_run(handle.id, RunLifecycleInput(1, str(run_id))) == original_id
        )
        before = brain.calls
        db.execute(
            "UPDATE outbox_events SET available_at=now() WHERE run_id=%s AND event_type='start_run'",
            (run_id,),
        )
        await outbox.run_once()
        assert brain.calls == before
        history = await handle.fetch_history()
        await Replayer(workflows=[AgentLifecycleWorkflow]).replay_workflow(history)
        if outcome == "completed":
            fixture_dir = os.getenv("W1C_HISTORY_DIR")
            if fixture_dir:
                from pathlib import Path

                Path(fixture_dir).mkdir(parents=True, exist_ok=True)
                Path(fixture_dir, f"lifecycle_{strategy}_completed.json").write_text(
                    history.to_json()
                )
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == outcome
    assert (
        db.execute(
            "SELECT count(*) FROM workflow_executions WHERE run_id=%s", (run_id,)
        ).fetchone()[0]
        == 1
    )
    assert (
        db.execute(
            "SELECT count(*) FROM account_execution_leases WHERE owner_run_id=%s", (run_id,)
        ).fetchone()[0]
        == 0
    )
    assert (
        db.execute(
            "SELECT status FROM outbox_events WHERE run_id=%s AND event_type='start_run'", (run_id,)
        ).fetchone()[0]
        == "processed"
    )
    if outcome == "completed":
        assert db.execute(
            "SELECT content FROM messages WHERE produced_by_run_id=%s", (run_id,)
        ).fetchall() == [("canonical reply",)]
        assert sandbox.calls
