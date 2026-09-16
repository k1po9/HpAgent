from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import UUID

import pytest
from temporalio.exceptions import WorkflowAlreadyStartedError

from actions.contracts import ActionRequest
from actions.runtime import ActionRuntime
from agent_activities.control import TemporalActivityControl
from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import AGENT_SCHEMA_VERSION, ApprovalDecisionSignal
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow
from application.conversation import normalize_qq_message
from common.types import ChannelType, UnifiedMessage
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.artifact_workflow import ARTIFACT_TASK_QUEUE, ArtifactBuildWorkflow
from orchestration.config import TemporalConfig
from orchestration.document_workflow import NormalizeDocumentWorkflow
from orchestration.research_workflow import ResearchReportWorkflow, ResearchTaskScheduleWorkflow
from orchestration.run_lifecycle_contracts import (
    WEB_AGENT_TASK_QUEUE,
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_WORKFLOW_SCHEMA_VERSION,
    RunLifecycleInput,
)
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
    validate_standalone_web_worker_topology,
    validate_web_worker_startup,
)
from web_domain.lifecycle import LifecycleAuthority
from web_domain.run_events import RedisWebRunEventSinkFactory


def test_agent_queues_are_isolated_from_scheduled_memory_queue():
    config = TemporalConfig()
    assert config.task_queue == "hpagent-task-queue"
    assert config.web_lifecycle_task_queue == WEB_LIFECYCLE_TASK_QUEUE
    assert config.web_agent_task_queue == WEB_AGENT_TASK_QUEUE
    assert len({config.task_queue, config.web_lifecycle_task_queue, config.web_agent_task_queue}) == 3
    assert config.web_real_agent_enabled is False


def test_web_worker_startup_fails_closed_without_c07_gate_or_database():
    config = TemporalConfig()
    with pytest.raises(RuntimeError):
        validate_web_worker_startup(config, "postgresql://worker")
    config.web_real_agent_gate_version = WEB_REAL_AGENT_GATE_VERSION
    with pytest.raises(RuntimeError):
        validate_web_worker_startup(config, None)
    validate_web_worker_startup(config, "postgresql://worker")
    config.agent_execution_lease_ttl_seconds = 660
    with pytest.raises(RuntimeError, match="greater than 660 seconds"):
        validate_web_worker_startup(config, "postgresql://worker")
    config.agent_execution_lease_ttl_seconds = 900
    config.web_finalize_start_to_close_seconds = 21
    with pytest.raises(RuntimeError, match="frozen Workflow contract"):
        validate_web_worker_startup(config, "postgresql://worker")


def test_standalone_web_worker_fails_closed_under_single_process_account_lock():
    # P0-2: a separate Web Worker process racing the main Worker for the
    # workspace process lock (and splitting the AccountLockRegistry) must be a
    # hard configuration error, never a silently-duplicated QQ deployment.
    with pytest.raises(RuntimeError, match="single_process_account_lock"):
        validate_standalone_web_worker_topology("single_process_account_lock", True)
    with pytest.raises(RuntimeError, match="must share one process"):
        validate_standalone_web_worker_topology("single_process_account_lock", False)


def test_standalone_web_worker_requires_real_agent_gate():
    with pytest.raises(RuntimeError, match="WEB_REAL_AGENT_ENABLED=true"):
        validate_standalone_web_worker_topology("session_worktree", False)


def test_standalone_web_worker_accepts_session_worktree_with_gate():
    # session_worktree is the only topology where a separate Web Worker process
    # is structurally valid; with the gate on, the entrypoint may proceed.
    validate_standalone_web_worker_topology("session_worktree", True)


def test_web_workflow_input_is_minimal_and_versioned():
    assert list(RunLifecycleInput.__dataclass_fields__) == ["schema_version", "run_id"]
    RunLifecycleInput(WEB_WORKFLOW_SCHEMA_VERSION, "run-1").validate()
    with pytest.raises(Exception):
        RunLifecycleInput(2, "run-1").validate()
    with pytest.raises(Exception):
        RunLifecycleInput(WEB_WORKFLOW_SCHEMA_VERSION, "").validate()


def test_worker_composition_uses_two_web_task_queues(monkeypatch):
    made: list[dict] = []

    class FakeWorker:
        def __init__(self, _client, **kwargs):
            made.append(kwargs)

    monkeypatch.setattr("orchestration.web_workers.Worker", FakeWorker)
    workers = build_web_temporal_workers(object(), lifecycle_activities=[], agent_activities=[])
    assert workers.lifecycle is not workers.agent
    assert made[0]["task_queue"] == WEB_LIFECYCLE_TASK_QUEUE
    assert made[0]["workflows"] == [
        AgentLifecycleWorkflow,
        ResearchReportWorkflow,
        ResearchTaskScheduleWorkflow,
        ArtifactBuildWorkflow,
        NormalizeDocumentWorkflow,
    ]
    assert made[1]["task_queue"] == WEB_AGENT_TASK_QUEUE
    assert made[1]["workflows"] == [
        AgentRunWorkflow,
        ReactAgentWorkflow,
        PlanAndExecuteWorkflow,
        AgentStepWorkflow,
        ToolExecutionWorkflow,
    ]


def test_artifact_uses_the_registered_web_lifecycle_task_queue():
    assert ARTIFACT_TASK_QUEUE == WEB_LIFECYCLE_TASK_QUEUE


@pytest.mark.asyncio
async def test_lifecycle_activity_adapter_loads_authority_only_by_run_id():
    from orchestration.run_lifecycle_activities import inject_run_lifecycle, prepare_run_activity

    class FakeLifecycle:
        def prepare(self, run_id):
            assert str(run_id) == "00000000-0000-0000-0000-000000000001"
            return LifecycleAuthority(str(run_id), "running")

    inject_run_lifecycle(FakeLifecycle())
    result = await prepare_run_activity(RunLifecycleInput(
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
            assert request == RunLifecycleInput(1, run_id)
            return "temporal-run"

        async def cancel_web_run(self, workflow_id):
            assert workflow_id == f"hpagent-web-run-{run_id}"
            return True

    store = Store()
    assert await TemporalOutboxDispatcher(store, Temporal()).dispatch_start(UUID(run_id))
    assert store.cancel_requested is True


@pytest.mark.asyncio
async def test_approval_dispatch_uses_persisted_deterministic_routing_identity():
    received = []

    class Temporal:
        async def signal_tool_approval(self, workflow_id, signal):
            received.append((workflow_id, signal))

    payload = {
        "approval_id": "00000000-0000-0000-0000-000000000042",
        "operation_id": "run:tool:one",
        "tool_execution_workflow_id": "hpagent-tool-exact",
    }
    assert await TemporalOutboxDispatcher(object(), Temporal()).dispatch_approval_decision(
        payload
    )
    assert received == [(
        "hpagent-tool-exact",
        ApprovalDecisionSignal(AGENT_SCHEMA_VERSION, payload["approval_id"], payload["operation_id"]),
    )]


@pytest.mark.asyncio
async def test_temporal_adapter_recovers_already_started_with_same_identity():
    temporal_run_id = "10000000-0000-0000-0000-000000000001"

    class Client:
        async def start_workflow(self, *args, **kwargs):
            raise WorkflowAlreadyStartedError(
                kwargs["id"], "AgentLifecycleWorkflow", run_id=temporal_run_id
            )

    request = RunLifecycleInput(1, "00000000-0000-0000-0000-000000000001")
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
async def test_temporal_start_temporarily_unavailable_is_retried_then_processed():
    calls: list[str] = []
    events: list[dict] = [{
        "outbox_event_id": "00000000-0000-0000-0000-000000000011",
        "run_id": "00000000-0000-0000-0000-000000000001",
        "event_type": "start_run",
        "attempt_count": 1,
    }]

    class Outbox:
        def claim(self, worker_id, event_types, limit):
            return events

        def mark_processed(self, event_id, worker_id):
            calls.append("processed")

        def mark_retryable_failure(self, event_id, worker_id, code, message, retry_at):
            assert code == "temporal_dispatch_failed"
            assert retry_at > datetime.now(UTC)
            calls.append("retryable")
            events[0] = {**events[0], "attempt_count": 2}
            return True

        def dead_letter(self, *args):
            raise AssertionError("a transient Temporal failure must not dead-letter")

    class Dispatcher:
        attempts = 0

        async def dispatch_start(self, run_id):
            self.attempts += 1
            if self.attempts == 1:
                raise ConnectionError("Temporal Start temporarily unavailable")
            return True

    consumer = WebOutboxDispatcher(Outbox(), Dispatcher(), "worker", max_attempts=3)
    # First poll: the transient failure is marked retryable, not dead-lettered.
    assert await consumer.run_once() == 1
    assert calls == ["retryable"]
    assert events[0]["attempt_count"] == 2

    # Second poll: the retried claim succeeds and the event is marked processed.
    calls.clear()
    assert await consumer.run_once() == 1
    assert calls == ["processed"]


def test_qq_ingress_key_is_derived_from_protocol_identity():
    message = UnifiedMessage(
        message_id="random-local-id", sender_id="sender", channel_type=ChannelType.NAPCAT,
        content="hello", metadata={"detail_type": "private", "self_id": "bot", "message_id": "qq-message-1"},
    )
    source = normalize_qq_message(message, "napcat")
    message.message_id = "another-random-local-id"
    assert normalize_qq_message(message, "napcat").message_key == source.message_key
    assert source.origin["external_message_id"] == "qq-message-1"


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
            return StartDecision(run_id, web_workflow_id(run_id), False)

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
