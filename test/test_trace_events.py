from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from agent_activities.runtime import DurableAgentActivities
from agent_activities.store import ToolOperationState
from agent_execution.facade import ExecutionRequest
from agent_execution.tracing import (
    TraceEvent,
    TraceEventNode,
    TraceLifecycleObserver,
    TraceRun,
    TraceTree,
    model_observation_metadata,
    trace_node_id,
)
from agent_execution.tracing.sink import TraceEventSink, TracingWebEventSinkFactory
from agent_execution.web_events import RedisWebRunEventSinkFactory
from agent_execution.web_host import WebExecutionHost
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    CompactToolCall,
    ContextBootstrapInput,
    ToolExecutionInput,
)
from web_api.queries import trace_tree_dto


@pytest.mark.asyncio
async def test_trace_sink_persists_and_projects_start_and_end_events(monkeypatch):
    run_id, node_id = uuid4(), uuid4()
    writes: list[tuple] = []
    published: list[dict] = []

    class Repository:
        def create_trace_run(self, *args):
            writes.append(("run", *args))

        def start_event(self, *args):
            writes.append(("start", *args))

        def finish_event(self, *args):
            writes.append(("end", *args))

            class Event:
                duration_ms = 42

            return Event()

    class Redis:
        async def publish(self, _topic, payload):
            published.append(json.loads(payload))

    async def in_process(function, *args):
        return function(*args)

    monkeypatch.setattr("agent_execution.tracing.sink.asyncio.to_thread", in_process)

    downstream = RedisWebRunEventSinkFactory(Redis()).for_run(str(run_id))
    sink = TraceEventSink(str(run_id), downstream, Repository())

    await sink.started()
    await sink.trace_start(str(node_id), None, "AgentExecution", "agent", {"safe": True})
    await sink.trace_end(str(node_id), "completed", {"turns": 1})

    assert [item[0] for item in writes] == ["run", "start", "end"]
    assert [item["event_type"] for item in published] == [
        "run.started",
        "trace.event",
        "trace.event",
    ]
    assert published[1]["payload"]["action"] == "start"
    assert published[2]["payload"]["duration_ms"] == 42


@pytest.mark.asyncio
async def test_trace_persistence_failure_does_not_block_online_projection(monkeypatch):
    run_id, node_id = uuid4(), uuid4()
    published: list[dict] = []

    class Repository:
        def start_event(self, *_args):
            raise ConnectionError("database unavailable")

    class Redis:
        async def publish(self, _topic, payload):
            published.append(json.loads(payload))

    async def in_process(function, *args):
        return function(*args)

    monkeypatch.setattr("agent_execution.tracing.sink.asyncio.to_thread", in_process)

    sink = TraceEventSink(
        str(run_id), RedisWebRunEventSinkFactory(Redis()).for_run(str(run_id)), Repository()
    )
    await sink.trace_start(str(node_id), None, "LLMCall", "llm")

    assert sink.degraded is True
    assert published[0]["event_type"] == "trace.event"


@pytest.mark.asyncio
async def test_invalid_trace_node_id_only_degrades_persistence():
    run_id = uuid4()
    published: list[dict] = []

    class Repository:
        pass

    class Redis:
        async def publish(self, _topic, payload):
            published.append(json.loads(payload))

    sink = TraceEventSink(
        str(run_id), RedisWebRunEventSinkFactory(Redis()).for_run(str(run_id)), Repository()
    )
    await sink.trace_start("runtime-node", None, "ToolExecution", "tool")

    assert sink.degraded is True
    assert published[0]["payload"]["node_id"] == "runtime-node"


@pytest.mark.asyncio
async def test_tracing_factory_reuses_online_stream_until_root_terminal(monkeypatch):
    run_id = str(uuid4())
    payloads: list[dict] = []

    async def in_process(function, *args):
        return function(*args)

    monkeypatch.setattr("agent_execution.tracing.sink.asyncio.to_thread", in_process)

    class Repository:
        def create_trace_run(self, *_args):
            return object()

        def start_event(self, *_args):
            return object()

        def finish_event(self, *_args):
            return SimpleNamespace(duration_ms=1)

    class Redis:
        async def publish(self, _topic, payload):
            payloads.append(json.loads(payload))

    factory = TracingWebEventSinkFactory(
        RedisWebRunEventSinkFactory(Redis()), Repository()
    )
    first = factory.for_run(run_id)
    await first.progress("starting", "first activity")
    await first.close()
    second = factory.for_run(run_id)
    await second.progress("calling_model", "second activity")

    assert second is first
    assert payloads[0]["stream_id"] == payloads[1]["stream_id"]
    assert [item["event_seq"] for item in payloads] == [1, 2]

    root_id = trace_node_id(run_id, "agent_execution")
    await second.trace_start(root_id, None, "AgentExecution", "agent")
    await second.trace_end(root_id, "completed")

    assert factory.for_run(run_id) is not first


def test_trace_node_ids_are_stable_and_operation_scoped():
    run_id = str(uuid4())

    assert trace_node_id(run_id, "agent_execution") == trace_node_id(
        run_id, "agent_execution"
    )
    assert trace_node_id(run_id, "llm_call", "turn:1") != trace_node_id(
        run_id, "llm_call", "turn:2"
    )


def test_trace_tree_dto_exposes_safe_nested_http_contract():
    trace_run_id, run_id, account_id, conversation_id, root_id = (
        uuid4() for _ in range(5)
    )
    started_at = datetime(2026, 8, 22, tzinfo=UTC)
    tree = TraceTree(
        run=TraceRun(
            trace_run_id=trace_run_id,
            run_id=run_id,
            account_id=account_id,
            conversation_id=conversation_id,
            strategy="react",
            status="completed",
            started_at=started_at,
            ended_at=started_at,
            metadata={"source": "web"},
        ),
        roots=(
            TraceEventNode(
                TraceEvent(
                    trace_event_id=root_id,
                    trace_run_id=trace_run_id,
                    parent_event_id=None,
                    event_type="agent",
                    name="AgentExecution",
                    status="completed",
                    started_at=started_at,
                    ended_at=started_at,
                    duration_ms=10,
                    metadata={"strategy": "react"},
                )
            ),
        ),
    )

    dto = trace_tree_dto(tree)

    assert "account_id" not in dto["run"]
    assert dto["run"]["run_id"] == str(run_id)
    assert dto["roots"][0]["event"]["trace_event_id"] == str(root_id)
    assert dto["roots"][0]["event"]["metadata"] == {"strategy": "react"}


def test_model_observation_excludes_content_and_keeps_usage():
    decision = SimpleNamespace(
        content="must not be retained",
        raw_response=SimpleNamespace(
            content="also private",
            model="model-a",
            provider="provider-a",
            endpoint_id="chat-primary",
            usage={"prompt_tokens": 10, "completion_tokens": 4},
        ),
    )

    assert model_observation_metadata(decision) == {
        "model": "model-a",
        "provider": "provider-a",
        "endpoint_id": "chat-primary",
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 4},
    }


def test_terminal_observer_creates_and_closes_the_stable_root():
    run_id = uuid4()
    calls: list[tuple] = []

    class Repository:
        def start_event(self, *args):
            calls.append(("start", *args))

        def finish_event(self, *args):
            calls.append(("end", *args))

    TraceLifecycleObserver(Repository()).observe_terminal(
        run_id, "failed", {"error_code": "model_unavailable"}
    )

    expected = trace_node_id(str(run_id), "agent_execution")
    assert str(calls[0][2]) == expected
    assert str(calls[1][2]) == expected
    assert calls[1][3] == "failed"


@pytest.mark.asyncio
async def test_context_activity_emits_root_memory_llm_and_context_nodes(monkeypatch):
    async def in_process(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("agent_activities.runtime.asyncio.to_thread", in_process)
    run_id, account_id, conversation_id, session_id = (str(uuid4()) for _ in range(4))
    recorded: list[tuple] = []

    class Events:
        async def progress(self, phase, summary):
            return None

        async def trace_start(self, node_id, parent_id, name, node_type, metadata=None):
            recorded.append(("start", node_id, parent_id, name, node_type, metadata))

        async def trace_end(self, node_id, status, metadata=None):
            recorded.append(("end", node_id, status, metadata))

        async def close(self):
            recorded.append(("close",))

    events = Events()

    class EventFactory:
        def for_run(self, _run_id):
            return events

    class Store:
        def begin_operation(self, *_args):
            return None

        def create_transcript(self, **_kwargs):
            return 1

        def fail_operation(self, *_args):
            raise AssertionError("context should not fail")

    class Memory:
        async def recall_long_term(self, query):
            assert query == "rewritten"
            return ("memory",)

        def compose(self, memories):
            assert memories == ("memory",)
            return ({"role": "user", "content": "hello"},)

    request = ContextBootstrapInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        account_id,
        conversation_id,
        session_id,
        "react",
        f"{run_id}:react:context",
    )

    class Loader:
        async def load(self, _run_id):
            return ExecutionRequest(
                run_id,
                account_id,
                conversation_id,
                session_id,
                "hello",
                ({"role": "user", "content": "hello"},),
                context_provider=Memory(),
            )

    class Brain:
        async def rewrite_recall_query(self, **_kwargs):
            return "rewritten", [{"role": "user", "content": "hello"}]

    activities = DurableAgentActivities(
        store=Store(),
        loader=Loader(),
        brain=Brain(),
        actions=object(),
        event_factory=EventFactory(),
        resource_prep=object(),
        lifecycle=None,
    )
    result = await activities.context_bootstrap(request)

    assert result.transcript_version == 1
    assert [item[3] for item in recorded if item[0] == "start"] == [
        "AgentExecution",
        "ContextAssembly",
        "MemoryQueryRewrite",
        "MemoryRecall",
    ]
    completed = [item for item in recorded if item[0] == "end"]
    assert [item[2] for item in completed] == ["completed", "completed", "completed"]
    assert completed[1][3]["memory_count"] == 1


@pytest.mark.asyncio
async def test_deduplicated_tool_activity_still_projects_tool_node(monkeypatch):
    async def in_process(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr("agent_activities.runtime.asyncio.to_thread", in_process)
    run_id, account_id, conversation_id, session_id = (str(uuid4()) for _ in range(4))
    operation_id = f"{run_id}:react:turn:1:tool:call-1"
    recorded: list[tuple] = []

    class Events:
        async def trace_start(self, node_id, parent_id, name, node_type, metadata=None):
            recorded.append(("start", node_id, name, metadata))

        async def trace_end(self, node_id, status, metadata=None):
            recorded.append(("end", node_id, status, metadata))

        async def close(self):
            recorded.append(("close",))

    class EventFactory:
        def for_run(self, _run_id):
            return Events()

    class Store:
        def begin_tool_operation(self, _operation_id, _run_id):
            return ToolOperationState(
                "completed",
                {
                    "schema_version": AGENT_SCHEMA_VERSION,
                    "operation_id": operation_id,
                    "result_ref": f"agent-tool-result:{operation_id}",
                    "transcript_version": 2,
                    "display_summary": "done",
                },
            )

    request = ToolExecutionInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        account_id,
        conversation_id,
        session_id,
        "react",
        f"agent-transcript:{run_id}",
        1,
        1,
        operation_id,
        1,
        CompactToolCall("call-1", "read_tool", "decision#call-1"),
    )
    activities = DurableAgentActivities(
        store=Store(),
        loader=None,
        brain=None,
        actions=object(),
        event_factory=EventFactory(),
        resource_prep=object(),
        lifecycle=None,
    )

    result = await activities.tool_execution(request)

    assert result.transcript_version == 2
    assert recorded[0][0] == "start"
    assert recorded[0][2] == "ToolExecution"
    assert recorded[0][3]["tool_name"] == "read_tool"
    assert recorded[1][0] == "end"
    assert recorded[1][2:] == ("completed", {"deduplicated": True})


@pytest.mark.asyncio
async def test_web_host_closes_agent_root_after_authoritative_reply():
    run_id = str(uuid4())
    recorded: list[tuple] = []

    class Loader:
        async def load(self, _run_id):
            return ExecutionRequest(run_id, str(uuid4()), str(uuid4()), str(uuid4()), "hi", ())

    class Facade:
        async def execute(self, request, control, events, audit):
            return SimpleNamespace(content="done", tool_turns=1, memory_observations=())

    class Events:
        async def started(self):
            return None

        async def progress(self, phase, summary):
            return None

        async def trace_start(self, node_id, parent_id, name, node_type, metadata=None):
            recorded.append(("start", node_id, parent_id, name))

        async def trace_end(self, node_id, status, metadata=None):
            recorded.append(("end", node_id, status, metadata))

        async def close(self):
            recorded.append(("close",))

    class EventFactory:
        def for_run(self, _run_id):
            return Events()

    class Replies:
        async def complete(self, _run_id, result):
            assert result.content == "done"

    class Control:
        def cancelled(self):
            return False

    result = await WebExecutionHost(
        Loader(), Facade(), EventFactory(), Replies(), Control()
    ).execute(run_id)

    assert result.content == "done"
    assert recorded[0][0] == "start"
    assert recorded[0][2:] == (None, "AgentExecution")
    assert recorded[1][0] == "end"
    assert recorded[1][2] == "completed"
    assert recorded[-1] == ("close",)
