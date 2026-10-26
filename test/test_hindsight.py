"""Exercise the installed official SDK on an HTTP fixture, including wire failures."""

import asyncio

import pytest
from aiohttp import web

from memory.hindsight_client import (
    HindsightClient,
    HindsightContractError,
    HindsightRejected,
    HindsightUnavailable,
    HindsightUncertain,
    MemoryItem,
)
from memory.scope_policy import MemoryScopeError, chat_scope, memory_scope


@pytest.fixture
async def wire():
    calls = []
    reply = {"status": 200, "body": {}}

    async def handler(request):
        body = await request.json() if request.can_read_body else None
        calls.append((request.method, request.path, body))
        if reply.get("handler"):
            return await reply["handler"](request, body)
        await asyncio.sleep(reply.get("delay", 0))
        return web.json_response(reply["body"], status=reply["status"])

    app = web.Application()
    app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    yield HindsightClient(f"http://127.0.0.1:{port}"), calls, reply
    await runner.cleanup()


@pytest.mark.parametrize(
    "scope,group", [("", ""), ("unknown", ""), ("group", ""), ("private", "A")]
)
async def test_missing_scope_never_sends(wire, scope, group):
    client, calls, _ = wire
    with pytest.raises(MemoryScopeError):
        await client.recall("q", "u", scope=scope, group_id=group)
    with pytest.raises(MemoryScopeError):
        await client.retain_document([{"content": "x"}], "u", "doc", scope=scope, group_id=group)
    assert calls == []


def test_trusted_scope_mapping():
    assert chat_scope("u", {}).tags == ("scope:private",)
    assert chat_scope(
        "u", {"channel_type": "napcat", "scope": "guild", "context_key": "hash"}
    ).observation_scopes == [["scope:group", "group:hash"]]
    with pytest.raises(MemoryScopeError):
        chat_scope("u", {"channel_type": "napcat"})
    with pytest.raises(MemoryScopeError):
        memory_scope("", "private")


async def test_recall_wire_and_source_evidence(wire):
    client, calls, reply = wire
    fact = dict(id="f", text="fact", type="world", document_id="d", tags=["scope:private"])
    reply["body"] = {
        "results": [
            dict(
                id="o",
                text="observation",
                type="observation",
                tags=["scope:private"],
                source_fact_ids=["f"],
            )
        ],
        "source_facts": {"f": fact},
    }
    items = await client.recall("query", "u", scope="private")
    assert items[0].memory_id == "o"
    assert all(items[0].source_facts[0][k] == v for k, v in fact.items())
    assert items[0].bank_id == "hpagent-u-u"
    assert calls[0][1].endswith("/hpagent-u-u/memories/recall")
    assert calls[0][2]["tags_match"] == "all_strict"
    assert calls[0][2]["tags"] == ["scope:private"]
    reply["body"]["source_facts"] = {}
    assert await client.recall("q", "u", scope="private") == []
    reply["body"]["source_facts"] = {"f": {**fact, "tags": ["scope:group", "group:A"]}}
    with pytest.raises(HindsightContractError):
        await client.recall("q", "u", scope="private")


@pytest.mark.parametrize(
    "status,error",
    [
        (401, HindsightRejected),
        (404, HindsightRejected),
        (422, HindsightRejected),
        (429, HindsightUnavailable),
        (503, HindsightUnavailable),
    ],
)
async def test_errors_are_typed_and_not_retried(wire, status, error):
    client, calls, reply = wire
    reply.update(status=status, body={"detail": "secret body"})
    with pytest.raises(error) as raised:
        await client.recall("q", "u", scope="private")
    assert raised.value.status == status
    assert "secret" not in str(raised.value)
    assert len(calls) == 1


async def test_old_bank_recall_gate(wire):
    client, calls, _ = wire
    client.recall_enabled = False
    assert await client.recall("q", "u", scope="private") == []
    assert calls == []


def test_dto_keeps_document_and_identity():
    item = MemoryItem.from_dict({"content": "x", "id": "f", "document_id": "d"})
    assert (item.memory_id, item.source_session_id) == ("f", "d")


async def test_retired_reflect_has_no_io(wire):
    client, calls, _ = wire
    assert await client.reflect("u") == 0
    assert calls == []


async def test_transport_timeout_distinguishes_unknown_write(wire):
    client, calls, reply = wire
    reply["delay"] = 0.05
    client.recall_timeout = client.retain_timeout = 0.01
    with pytest.raises(HindsightUnavailable):
        await client.recall("q", "u", scope="private")
    with pytest.raises(HindsightUncertain):
        await client.retain_document(
            [{"content": "x"}], "u", "d", scope="private", iso_timestamp="2026-10-25T00:00:00Z"
        )
    assert len(calls) == 2


async def test_server_write_failure_requires_reconciliation(wire):
    client, calls, reply = wire
    reply.update(status=503, body={"detail": "body"})
    with pytest.raises(HindsightUncertain):
        await client.retain_document(
            [{"content": "x"}], "u", "d", scope="private", iso_timestamp="2026-10-25T00:00:00Z"
        )
    assert len(calls) == 1


async def test_malformed_success_is_a_contract_error(wire):
    client, _, reply = wire
    reply["body"] = {"results": "invalid"}
    with pytest.raises(HindsightContractError):
        await client.recall("q", "u", scope="private")


async def test_mixed_domain_result_is_rejected(wire):
    client, _, reply = wire
    reply["body"] = {
        "results": [
            dict(
                id="f",
                text="x",
                type="world",
                document_id="d",
                tags=["scope:private", "scope:group", "group:A"],
            )
        ]
    }
    with pytest.raises(HindsightContractError):
        await client.recall("q", "u", scope="private")


async def test_obsolete_schedule_is_removed_without_listing_accounts():
    from orchestration.memory_schedules import setup_reflect_schedule

    removed = []

    class Client:
        def get_schedule_handle(self, schedule_id):
            assert schedule_id == "hpagent-reflect-schedule"
            return self

        async def delete(self):
            removed.append(True)

    await setup_reflect_schedule(Client(), None, None)
    assert removed == [True]


async def test_memory_workflows_remain_valid_in_temporal_sandbox():
    from temporalio import workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from memory.workflows import MetricsReportWorkflow, ReflectWorkflow

    runner = SandboxedWorkflowRunner()
    for definition in (MetricsReportWorkflow, ReflectWorkflow):
        runner.prepare_workflow(workflow._Definition.must_from_class(definition))
