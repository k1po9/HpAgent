"""Retain source identity and fixed payload through the installed official SDK."""

from uuid import uuid4

import pytest
from test_hindsight import wire as wire

from memory.scope_policy import MemoryScopeError


async def test_async_retain_fixed_wire_payload(wire):
    client, calls, reply = wire
    operation = str(uuid4())
    reply["body"] = dict(
        success=True,
        bank_id="hpagent-u-u",
        items_count=1,
        **{"async": True},
        operation_id=operation,
    )
    for _ in range(2):
        receipt = await client.retain_document(
            [{"role": "user", "content": "hello"}, {"role": "assistant", "content": "reply"}],
            "u",
            "chat-run:1",
            scope="group",
            group_id="route-hash",
            async_retain=True,
            operation_id=operation,
            iso_timestamp="2026-10-25T01:02:03+00:00",
            metadata={"missing": None, "number": 1, "run_id": "1"},
        )
        assert receipt.accepted and receipt.operation_id == operation
    assert calls[0] == calls[1]
    body = calls[0][2]
    assert body["operation_id"] == operation and body["async"]
    item = body["items"][0]
    assert item["tags"] == ["scope:group", "group:route-hash"]
    assert item["observation_scopes"] == [item["tags"]]
    assert item["metadata"] == {"number": "1", "run_id": "1"}
    assert item["timestamp"] == "2026-10-25T01:02:03+00:00"
    assert item["content"] == "[user]: hello\n\n[assistant]: reply"


@pytest.mark.parametrize("stamp", ["", "2026-10-25", "bad"])
async def test_source_time_is_required_before_request(wire, stamp):
    client, calls, _ = wire
    with pytest.raises(MemoryScopeError):
        await client.retain_document(
            [{"content": "x"}], "u", "d", scope="private", iso_timestamp=stamp
        )
    assert calls == []


async def test_async_identity_required_before_request(wire):
    client, calls, _ = wire
    with pytest.raises(MemoryScopeError):
        await client.retain_document(
            [{"content": "x"}],
            "u",
            "d",
            scope="private",
            iso_timestamp="2026-10-25T00:00:00Z",
            async_retain=True,
        )
    assert calls == []
