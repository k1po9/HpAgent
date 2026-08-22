from __future__ import annotations

import json
from uuid import uuid4

import pytest

from agent_execution.tracing.sink import TraceEventSink
from agent_execution.web_events import RedisWebRunEventSinkFactory


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
