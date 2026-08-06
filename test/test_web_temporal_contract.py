from __future__ import annotations

import ast
import asyncio
import inspect
from uuid import UUID

import pytest
from temporalio.common import RetryPolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from actions.runtime import ActionRuntime
from agent.protocol import ActionRequest, ActionResult
from agent_execution.brain_action_loop import DefaultBrainActionLoop
from agent_execution.facade import (
    AgentExecutionFacade,
    ExecutionRequest,
    ExecutionResult,
    NullExecutionAuditSink,
    StableExecutionFailure,
)
from agent_execution.qq_host import QQExecutionHost, qq_execution_id
from agent_execution.web_adapters import TemporalActivityControl
from agent_execution.web_events import RedisWebRunEventSinkFactory
from agent_execution.web_host import WebExecutionHost
from application.conversation import ConversationService
from common.types import ChannelType, UnifiedMessage
from orchestration.config import TemporalConfig
from orchestration.web_dispatcher import (
    StartDecision,
    TemporalClientAdapter,
    TemporalOutboxDispatcher,
    WebOutboxDispatcher,
    web_workflow_id,
)
from orchestration.web_reconciler import ReconcileCandidate, TemporalFact, WebRunReconciler
from orchestration.web_workers import (
    WEB_REAL_AGENT_GATE_VERSION,
    build_web_temporal_workers,
    validate_web_worker_startup,
)
from orchestration.web_workflow import (
    _AGENT_NO_RETRY,
    WEB_AGENT_TASK_QUEUE,
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_WORKFLOW_SCHEMA_VERSION,
    WebRunWorkflow,
    WebRunWorkflowInput,
)
from web_domain.lifecycle import LifecycleAuthority


def test_web_temporal_defaults_are_isolated_from_qq_queue():
    config = TemporalConfig()
    assert config.task_queue == "hpagent-task-queue"
    assert config.web_lifecycle_task_queue == WEB_LIFECYCLE_TASK_QUEUE
    assert config.web_agent_task_queue == WEB_AGENT_TASK_QUEUE
    assert len({config.task_queue, config.web_lifecycle_task_queue, config.web_agent_task_queue}) == 3
    assert config.web_agent_heartbeat_timeout_seconds == 45
    assert config.web_cancel_cleanup_timeout_seconds == 30
    assert config.web_real_agent_enabled is False


def test_web_worker_startup_fails_closed_without_c07_gate_or_database():
    config = TemporalConfig()
    with pytest.raises(RuntimeError):
        validate_web_worker_startup(config, "postgresql://worker")
    config.web_real_agent_gate_version = WEB_REAL_AGENT_GATE_VERSION
    with pytest.raises(RuntimeError):
        validate_web_worker_startup(config, None)
    validate_web_worker_startup(config, "postgresql://worker")
    config.web_agent_heartbeat_timeout_seconds = 44
    with pytest.raises(RuntimeError, match="frozen Workflow contract"):
        validate_web_worker_startup(config, "postgresql://worker")


def test_web_workflow_input_is_minimal_and_versioned():
    assert list(WebRunWorkflowInput.__dataclass_fields__) == ["schema_version", "run_id"]
    WebRunWorkflowInput(WEB_WORKFLOW_SCHEMA_VERSION, "run-1").validate()
    with pytest.raises(Exception):
        WebRunWorkflowInput(2, "run-1").validate()
    with pytest.raises(Exception):
        WebRunWorkflowInput(WEB_WORKFLOW_SCHEMA_VERSION, "").validate()


def test_agent_activity_has_an_explicit_single_attempt_policy():
    assert isinstance(_AGENT_NO_RETRY, RetryPolicy)
    assert _AGENT_NO_RETRY.maximum_attempts == 1


def test_workflow_only_schedules_activities_and_uses_separate_agent_queue():
    source = inspect.getsource(WebRunWorkflow)
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert "WEB_LIFECYCLE_TASK_QUEUE" in names
    assert "WEB_AGENT_TASK_QUEUE" in names
    assert "execute_agent_activity" in source
    assert "finalize_failed_activity" in source
    assert "finalize_cancelled_activity" in source
    for forbidden in ("psycopg", "redis", "ChannelRouter", "ResourcePool", "open("):
        assert forbidden not in source


def test_worker_composition_uses_two_web_task_queues(monkeypatch):
    made: list[dict] = []

    class FakeWorker:
        def __init__(self, _client, **kwargs):
            made.append(kwargs)

    monkeypatch.setattr("orchestration.web_workers.Worker", FakeWorker)
    workers = build_web_temporal_workers(object(), lifecycle_activities=[], agent_activities=[])
    assert workers.lifecycle is not workers.agent
    assert made[0]["task_queue"] == WEB_LIFECYCLE_TASK_QUEUE
    assert made[0]["workflows"] == [WebRunWorkflow]
    assert made[1]["task_queue"] == WEB_AGENT_TASK_QUEUE
    assert made[1]["workflows"] == []


@pytest.mark.asyncio
async def test_lifecycle_activity_adapter_loads_authority_only_by_run_id():
    from orchestration.web_activities import inject_web_lifecycle, prepare_run_activity

    class FakeLifecycle:
        def prepare(self, run_id):
            assert str(run_id) == "00000000-0000-0000-0000-000000000001"
            return LifecycleAuthority(str(run_id), "running")

    inject_web_lifecycle(FakeLifecycle())
    result = await prepare_run_activity(WebRunWorkflowInput(
        WEB_WORKFLOW_SCHEMA_VERSION, "00000000-0000-0000-0000-000000000001"
    ))
    assert result == {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "status": "running",
    }


@pytest.mark.asyncio
async def test_dispatcher_uses_deterministic_id_and_rechecks_cancel_after_start():
    run_id = "00000000-0000-0000-0000-000000000001"

    class Store:
        cancel_requested = False

        def prepare_start(self, value):
            return StartDecision(value, web_workflow_id(value), True)

        def still_queued(self, value):
            return True

        def record_started(self, value, temporal_run_id):
            assert str(value) == run_id
            assert temporal_run_id == "temporal-run"

        def needs_cancel(self, value):
            return True

        def record_cancel_requested(self, value):
            self.cancel_requested = True

    class Temporal:
        async def start_web_run(self, workflow_id, request):
            assert workflow_id == f"hpagent-web-run-{run_id}"
            assert request == WebRunWorkflowInput(1, run_id)
            return "temporal-run"

        async def cancel_web_run(self, workflow_id):
            assert workflow_id == f"hpagent-web-run-{run_id}"
            return True

    store = Store()
    assert await TemporalOutboxDispatcher(store, Temporal()).dispatch_start(UUID(run_id))
    assert store.cancel_requested is True


@pytest.mark.asyncio
async def test_temporal_adapter_recovers_already_started_with_same_identity():
    temporal_run_id = "10000000-0000-0000-0000-000000000001"

    class Client:
        async def start_workflow(self, *args, **kwargs):
            raise WorkflowAlreadyStartedError(
                kwargs["id"], "WebRunWorkflow", run_id=temporal_run_id
            )

    request = WebRunWorkflowInput(1, "00000000-0000-0000-0000-000000000001")
    recovered = await TemporalClientAdapter(Client()).start_web_run(
        web_workflow_id(request.run_id), request
    )
    assert recovered == temporal_run_id


@pytest.mark.asyncio
async def test_cancel_not_found_uses_the_authoritative_lifecycle_finalizer():
    run_id = UUID("00000000-0000-0000-0000-000000000001")
    finalized: list[UUID] = []

    class Store:
        def current_workflow_id(self, value):
            return web_workflow_id(value)

    class Temporal:
        async def cancel_web_run(self, workflow_id):
            return False

    class Lifecycle:
        def finalize_cancelled(self, value):
            finalized.append(value)

    dispatcher = TemporalOutboxDispatcher(Store(), Temporal(), Lifecycle())
    assert await dispatcher.dispatch_cancel(run_id)
    assert finalized == [run_id]


@pytest.mark.asyncio
async def test_dispatcher_exhaustion_dead_letters_through_authoritative_outbox():
    calls: list[str] = []

    class Outbox:
        def claim(self, worker_id, event_types, limit):
            return [{
                "outbox_event_id": "00000000-0000-0000-0000-000000000010",
                "run_id": "00000000-0000-0000-0000-000000000001",
                "event_type": "start_run",
                "attempt_count": 3,
            }]

        def dead_letter(self, event_id, worker_id, code, message):
            calls.append(code)

        def mark_processed(self, *args):
            raise AssertionError("failed event cannot be processed")

        def mark_retryable_failure(self, *args):
            raise AssertionError("exhausted event cannot be retried")

    class Dispatcher:
        async def dispatch_start(self, run_id):
            raise ConnectionError("Temporal unavailable")

    consumer = WebOutboxDispatcher(Outbox(), Dispatcher(), "worker", max_attempts=3)
    assert await consumer.run_once() == 1
    assert calls == ["temporal_dispatch_exhausted"]


@pytest.mark.asyncio
async def test_facade_has_no_reply_sink_and_delegates_to_channel_neutral_loop():
    class Control:
        def cancelled(self):
            return False

    class Events:
        def for_run(self, run_id):
            return self

        async def progress(self, phase, summary):
            return None

    class Loop:
        async def execute(self, request, control, events, audit):
            return ExecutionResult(request.user_content, 0)

    result = await AgentExecutionFacade(Loop()).execute(
        ExecutionRequest("x", "a", "c", "s", "hello", ()), Control(), Events()
    )
    assert result == ExecutionResult("hello", 0)


@pytest.mark.asyncio
async def test_qq_host_uses_stable_message_identity_and_legacy_reply_sink():
    calls: list[str] = []
    message = {
        "message_id": "qq-message-1",
        "account_id": "account",
        "session_id": "session",
        "sender_id": "sender",
        "channel_type": "napcat",
        "content": "hello",
        "metadata": {},
    }
    expected = qq_execution_id("hpagent-account", "qq-message-1")

    class Loader:
        async def load(self, user_message, execution_id):
            assert user_message is message
            assert execution_id == expected
            return ExecutionRequest(
                execution_id,
                "account",
                "",
                "session",
                "hello",
                (),
                trigger_message_id="qq-message-1",
                interaction_profile="qq_private",
            )

    class Events:
        def for_execution(self, execution_id, user_message):
            return self

        async def progress(self, phase, summary):
            return None

    class Replies:
        async def complete(self, user_message, result):
            calls.append("reply")

    class Retention:
        async def retain(self, request, result, user_message):
            calls.append("retain")

    class Control:
        def cancelled(self):
            return False

    class Loop:
        async def execute(self, request, control, events, audit):
            calls.append(request.execution_id)
            return ExecutionResult("done", 2)

    host = QQExecutionHost(
        Loader(),
        AgentExecutionFacade(Loop()),
        Events(),
        Replies(),
        Control(),
        retention=Retention(),
    )
    result = await host.execute("hpagent-account", message)
    assert result == {
        "content": "done",
        "turns": 2,
        "session_id": "session",
        "account_id": "account",
    }
    assert calls == [expected, "reply", "retain"]


def test_qq_ingress_message_id_is_frozen_in_workflow_payload():
    service = object.__new__(ConversationService)
    service._idle_timeout_minutes = 5
    service._activity_timeout = 300
    message = UnifiedMessage(
        message_id="qq-message-1",
        sender_id="sender",
        channel_type=ChannelType.NAPCAT,
        content="hello",
    )
    payload = service._build_user_message(
        message=message,
        channel_type="napcat",
        session_id="session",
        account_id="account",
    )
    assert payload["message_id"] == "qq-message-1"


@pytest.mark.asyncio
async def test_web_host_loads_by_run_id_and_only_then_calls_complete():
    calls: list[str] = []

    class Loader:
        async def load(self, run_id):
            calls.append("load")
            return ExecutionRequest(run_id, "account", "conversation", "session", "hello", ())

    class Control:
        def cancelled(self):
            return False

    class Events:
        def for_run(self, run_id):
            return self

        async def progress(self, phase, summary):
            return None

    class Loop:
        async def execute(self, request, control, events, audit):
            calls.append("execute")
            return ExecutionResult("done", 1)

    class Replies:
        async def complete(self, run_id, result):
            calls.append("complete")
            assert run_id == "run"
            assert result.content == "done"

    host = WebExecutionHost(Loader(), AgentExecutionFacade(Loop()), Events(), Replies(), Control())
    assert (await host.execute("run")).content == "done"
    assert calls == ["load", "execute", "complete"]


@pytest.mark.asyncio
async def test_agent_activity_returns_only_after_host_completion():
    from orchestration.web_activities import execute_agent_activity, inject_web_execution_host

    class Host:
        async def execute(self, run_id):
            assert run_id == "00000000-0000-0000-0000-000000000001"
            return ExecutionResult("done", 0)

    inject_web_execution_host(Host())
    result = await execute_agent_activity(WebRunWorkflowInput(
        1, "00000000-0000-0000-0000-000000000001"
    ))
    assert result["status"] == "completed"


def test_temporal_execution_control_is_safe_outside_temporal_context():
    assert TemporalActivityControl().cancelled() is False


@pytest.mark.asyncio
async def test_redis_event_sink_is_run_scoped_and_degrades_without_failing_execution():
    published: list[tuple[str, str]] = []

    class Redis:
        async def publish(self, topic, payload):
            published.append((topic, payload))

    sink = RedisWebRunEventSinkFactory(Redis()).for_run("run-1")
    await sink.progress("starting", "start")
    await sink.progress("generating", "generate")
    assert [item[0] for item in published] == [
        "hpagent:web:run:run-1",
        "hpagent:web:run:run-1",
    ]
    assert '"event_seq":1' in published[0][1]
    assert '"event_seq":2' in published[1][1]
    await sink.close()
    await sink.progress("generating", "late")
    assert len(published) == 2

    class BrokenRedis:
        async def publish(self, topic, payload):
            raise ConnectionError("down")

    degraded = RedisWebRunEventSinkFactory(BrokenRedis()).for_run("run-2")
    await degraded.progress("starting", "start")
    await degraded.progress("generating", "ignored")
    assert degraded.degraded is True


@pytest.mark.asyncio
async def test_td_016_new_event_sink_never_reuses_a_previous_stream_id():
    import json

    payloads = []

    class Redis:
        async def publish(self, topic, payload):
            payloads.append(json.loads(payload))

    factory = RedisWebRunEventSinkFactory(Redis())
    first = factory.for_run("run")
    await first.progress("starting", "first worker")
    await first.close()
    second = factory.for_run("run")
    await second.progress("starting", "restarted worker")

    assert payloads[0]["stream_id"] != payloads[1]["stream_id"]
    assert payloads[0]["event_seq"] == payloads[1]["event_seq"] == 1


@pytest.mark.asyncio
async def test_brain_action_loop_uses_control_and_finishes_without_reply_sink():
    class Control:
        def cancelled(self):
            return False

    class Events:
        async def progress(self, phase, summary):
            return None

    class Actions:
        def reset_turn(self, session_id, execution_id):
            return None

        async def select_tools(self, **kwargs):
            return []

    class Decision:
        has_actions = False
        content = "done"

    class Brain:
        async def generate_chat_decision(self, **kwargs):
            assert kwargs["channel_overrides"] == {"timeout": 12}
            return Decision()

    result = await DefaultBrainActionLoop(Brain(), Actions()).execute(
        ExecutionRequest(
            "r",
            "a",
            "c",
            "s",
            "hello",
            ({"role": "user", "content": "hello"},),
            metadata={"channel_overrides": {"timeout": 12}},
        ),
        Control(), Events(), NullExecutionAuditSink(),
    )
    assert result == ExecutionResult(
        "done",
        1,
        (
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "done"},
        ),
    )


@pytest.mark.asyncio
async def test_web_context_rewrite_precedes_account_scoped_recall():
    calls: list[str] = []

    class Control:
        def cancelled(self):
            return False

    class Events:
        async def progress(self, phase, summary):
            return None

    class Context:
        async def recall_long_term(self, recall_query):
            calls.append(f"recall:{recall_query}")
            return ("memory",)

        def compose(self, memories):
            calls.append(f"compose:{memories[0]}")
            return ({"role": "user", "content": "isolated"},)

    class Actions:
        def reset_turn(self, session_id, execution_id):
            return None

        async def select_tools(self, **kwargs):
            return []

    class Decision:
        has_actions = False
        content = "done"

    class Brain:
        async def rewrite_recall_query(self, **kwargs):
            calls.append("rewrite")
            return "rewritten", ()

        async def generate_chat_decision(self, **kwargs):
            assert kwargs["messages"][0]["content"] == "isolated"
            return Decision()

    request = ExecutionRequest(
        "run",
        "account",
        "conversation",
        "session",
        "hello",
        (),
        context_provider=Context(),
    )
    result = await DefaultBrainActionLoop(Brain(), Actions()).execute(
        request, Control(), Events(), NullExecutionAuditSink()
    )
    assert result.content == "done"
    assert calls == ["rewrite", "recall:rewritten", "compose:memory"]


@pytest.mark.asyncio
async def test_brain_action_loop_cancels_an_inflight_model_call():
    class Control:
        checks = 0

        def cancelled(self):
            self.checks += 1
            return self.checks > 1

    class Events:
        async def progress(self, phase, summary):
            return None

    class Actions:
        def reset_turn(self, session_id, execution_id):
            return None

        async def select_tools(self, **kwargs):
            return []

    class Brain:
        async def generate_chat_decision(self, **kwargs):
            await asyncio.sleep(60)

    with pytest.raises(asyncio.CancelledError):
        await DefaultBrainActionLoop(Brain(), Actions()).execute(
            ExecutionRequest("r", "a", "c", "s", "hello", ()),
            Control(),
            Events(),
            NullExecutionAuditSink(),
        )


@pytest.mark.asyncio
async def test_action_runtime_cache_is_partitioned_and_cleared_by_execution():
    class Sandbox:
        async def select_tools(self, query, top_k):
            return ([{"name": query}], {"queries": [query]})

    class Sandboxes:
        def get_sandbox_for_session(self, session_id):
            return Sandbox()

    runtime = ActionRuntime(sandbox_manager=Sandboxes())
    first, second = await asyncio.gather(
        runtime.select_tools(
            user_content="first", session_id="session", execution_id="run-1"
        ),
        runtime.select_tools(
            user_content="second", session_id="session", execution_id="run-2"
        ),
    )
    assert first == [{"name": "first"}]
    assert second == [{"name": "second"}]
    assert set(runtime._tools_cache) == {"session:run-1", "session:run-2"}

    runtime.clear_execution("session", "run-1")
    assert set(runtime._tools_cache) == {"session:run-2"}
    runtime.clear_session("session")
    assert runtime._tools_cache == {}


@pytest.mark.asyncio
async def test_ae_030_tool_invocation_key_is_stable_for_activity_redelivery():
    runtime = ActionRuntime()

    async def execute(**kwargs):
        return {"output": "ok", "metadata": {}}

    runtime.execute = execute
    action = ActionRequest("tool-call", "tool", {})
    first = await runtime.execute_request(
        action, session_id="session", execution_id="stable-execution"
    )
    second = await runtime.execute_request(
        action, session_id="session", execution_id="stable-execution"
    )
    assert first.metadata["invocation_key"] == second.metadata["invocation_key"]
    assert first.metadata["invocation_key"] == (
        "tool-invocation:stable-execution:tool-call"
    )


@pytest.mark.asyncio
async def test_ae_028_side_effect_intent_audit_fails_closed_before_tool_execution():
    executed = []
    action = ActionRequest("call", "external_write", {})

    class Control:
        def cancelled(self):
            return False

    class Events:
        async def progress(self, phase, summary):
            return None

    class Actions:
        def reset_turn(self, session_id, execution_id):
            return None

        async def select_tools(self, **kwargs):
            return []

        def side_effect_class(self, session_id, tool_name):
            return "external_write"

        async def execute_request(self, request, **kwargs):
            executed.append(request.id)

    class Decision:
        has_actions = True
        content = ""
        action_requests = [action]
        stop_reason = "tool_use"
        input_context = {}

    class Brain:
        async def generate_chat_decision(self, **kwargs):
            return Decision()

    with pytest.raises(StableExecutionFailure) as failure:
        await DefaultBrainActionLoop(Brain(), Actions()).execute(
            ExecutionRequest("run", "a", "c", "s", "hello", ()),
            Control(),
            Events(),
            NullExecutionAuditSink(),
        )
    assert failure.value.code == "side_effect_audit_unavailable"
    assert executed == []


@pytest.mark.asyncio
async def test_td_022_cancel_hostile_tool_is_detached_within_cleanup_budget():
    late_results = []
    tool_started = {"value": False}

    class Control:
        def cancelled(self):
            return tool_started["value"]

    class Events:
        async def progress(self, phase, summary):
            return None

    class Actions:
        def reset_turn(self, session_id, execution_id):
            return None

        async def select_tools(self, **kwargs):
            return []

        async def execute_request(self, request, **kwargs):
            tool_started["value"] = True
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                # Simulate a cancellation-hostile thread/SDK returning late.
                await asyncio.sleep(0.1)
                late_results.append("ignored")
                return ActionResult(request=request, output="late")

    request = ActionRequest("tool-1", "unsafe_tool", {})

    class FirstDecision:
        has_actions = True
        content = ""
        action_requests = [request]

    class Brain:
        async def generate_chat_decision(self, **kwargs):
            return FirstDecision()

    loop = DefaultBrainActionLoop(
        Brain(),
        Actions(),
        cancel_cleanup_timeout_seconds=0.01,
        cancel_poll_interval_seconds=0.01,
    )
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(
            loop.execute(
                ExecutionRequest("run", "a", "c", "s", "hello", ()),
                Control(),
                Events(),
                NullExecutionAuditSink(),
            ),
            timeout=0.1,
        )
    await asyncio.sleep(0.15)
    assert late_results == ["ignored"]


@pytest.mark.asyncio
async def test_reconciler_uses_temporal_fact_without_holding_a_domain_lock():
    calls: list[tuple] = []

    class Store:
        def candidates(self, limit):
            return [ReconcileCandidate("run", "running", "workflow")]

        def finalize_failed(self, run_id, code, message):
            calls.append(("failed", run_id, code))

        def finalize_cancelled(self, run_id):
            calls.append(("cancelled", run_id))

        def record_temporal_fact(self, run_id, status):
            calls.append(("record", run_id, status))

    class Temporal:
        async def describe(self, workflow_id):
            calls.append(("describe", workflow_id))
            return TemporalFact("completed")

        async def cancel(self, workflow_id):
            calls.append(("cancel", workflow_id))

    assert await WebRunReconciler(Store(), Temporal()).run_once() == 1
    assert calls == [
        ("describe", "workflow"),
        ("record", "run", "completed"),
        ("failed", "run", "terminal_commit_missing"),
    ]


@pytest.mark.asyncio
async def test_td_003_dispatcher_does_not_start_a_run_cancelled_before_rpc():
    calls = []

    class Store:
        def prepare_start(self, run_id):
            return StartDecision(run_id, web_workflow_id(run_id), True)

        def still_queued(self, run_id):
            return False

    class Temporal:
        async def start_web_run(self, workflow_id, request):
            calls.append("start")
            return "temporal-run"

    run_id = UUID("00000000-0000-0000-0000-000000000003")
    assert await TemporalOutboxDispatcher(Store(), Temporal()).dispatch_start(run_id) is False
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("run_status", "temporal_status", "expected"),
    [
        ("running", "failed", ("failed", "internal_execution_error")),
        ("running", "timed_out", ("failed", "run_timeout")),
        ("running", "terminated", ("failed", "workflow_terminated")),
        ("running", "cancelled", ("cancelled",)),
        ("running", "not_found", ("failed", "terminal_commit_missing")),
        ("cancelling", "open", ("cancel",)),
        ("cancelling", "not_found", ("cancelled",)),
        ("completed", "open", ("cancel",)),
        ("completed", "failed", ("record", "failed")),
    ],
)
async def test_td_012_td_014_reconciler_converges_every_temporal_close_fact(
    run_status, temporal_status, expected
):
    calls = []

    class Store:
        def candidates(self, limit):
            return [ReconcileCandidate("run", run_status, "workflow")]

        def finalize_failed(self, run_id, code, message):
            calls.append(("failed", code))

        def finalize_cancelled(self, run_id):
            calls.append(("cancelled",))

        def record_temporal_fact(self, run_id, status):
            calls.append(("record", status))

    class Temporal:
        async def describe(self, workflow_id):
            return TemporalFact(temporal_status)

        async def cancel(self, workflow_id):
            calls.append(("cancel",))

    await WebRunReconciler(Store(), Temporal()).run_once()
    assert expected in calls
    if run_status == "completed":
        assert not any(call[0] == "failed" for call in calls)
