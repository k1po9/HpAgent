from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from resources.model_budget_context import model_budget_scope
from resources.model_client import ModelClient
from resources.model_failures import ModelRequestInvalid, classify_model_failure
from resources.resource_pool import ResourcePool


def transcript():
    return [
        {"role": "system", "content": "Use files"},
        {"role": "user", "content": "Read the files"},
        {"role": "assistant", "content": "Checking", "tool_calls": [
            {"id": "a", "name": "list_run_candidates", "arguments": {}},
            {"id": "b", "name": "read_file", "arguments": {"file": "资料.txt"}},
        ]},
        {"role": "tool", "tool_call_id": "a", "content": "[]"},
        {"role": "tool", "tool_call_id": "b", "content": "hello"},
        {"role": "assistant", "content": "Done"},
        {"role": "user", "content": "What next?"},
    ]


def client(api_format="openai"):
    return ModelClient({"api_key": "offline", "base_url": "https://example.test/v1",
                        "model": "test", "api_format": api_format})


@pytest.mark.parametrize("api_format", ["openai", "anthropic"])
def test_internal_parallel_round_trip_is_serialized_for_provider(api_format):
    messages = transcript()
    before = json.dumps(messages)
    body = client(api_format).prepare_request(messages).body()
    assert json.dumps(messages) == before
    if api_format == "openai":
        calls = body["messages"][2]["tool_calls"]
        assert calls[0] == {"id": "a", "type": "function",
                            "function": {"name": "list_run_candidates", "arguments": "{}"}}
        assert json.loads(calls[1]["function"]["arguments"]) == {"file": "资料.txt"}
        assert body["messages"][-1] == {"role": "user", "content": "What next?"}
    else:
        assert body["system"] == "Use files"
        assert [m["role"] for m in body["messages"]] == ["user", "assistant", "user", "assistant", "user"]
        calls = body["messages"][1]["content"]
        assert calls[1]["type"] == "tool_use"
        assert calls[2]["input"] == {"file": "资料.txt"}
        assert [b["tool_use_id"] for b in body["messages"][2]["content"]] == ["a", "b"]


def test_anthropic_explicit_results_convert_without_guessing_user_intent():
    messages = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "x", "name": "read", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "x", "content": "done"}]},
        {"role": "user", "content": "This is a real user message"},
    ]
    converted = client().prepare_request(messages).body()["messages"]
    assert converted[1]["role"] == "tool"
    assert converted[2]["role"] == "user"


@pytest.mark.parametrize("messages", [
    [{"role": "tool", "tool_call_id": "orphan", "content": "x"}],
    transcript()[:3],
    transcript()[:3] + [{"role": "user", "content": "Must not become a tool result"}],
    transcript()[:4] + [transcript()[3]],
    [{"role": "assistant", "tool_calls": [{"id": "x", "name": "f", "arguments": "bad-json"}]}],
    [{"role": "assistant", "tool_calls": [{"id": "x", "name": "f", "arguments": []}]}],
    [{"role": "assistant", "tool_calls": [{"id": "x", "name": "f"}, {"id": "x", "name": "g"}]}],
])
@pytest.mark.parametrize("api_format", ["openai", "anthropic"])
def test_invalid_round_trip_is_rejected_before_dispatch(messages, api_format):
    with pytest.raises(ModelRequestInvalid):
        client(api_format).prepare_request(messages)


async def test_governed_invalid_request_never_reserves_or_freezes():
    touched = []
    entitlement = SimpleNamespace(model_access_tier="standard", version=1)
    pool = ResourcePool(None,
        entitlement_service=SimpleNamespace(get=lambda _: SimpleNamespace(state=SimpleNamespace(value="valid"), entitlement=entitlement)),
        budget_coordinator=SimpleNamespace(reserve=lambda *a, **kw: touched.append("reserve")),
        snapshot_repository=SimpleNamespace(freeze=lambda **kw: touched.append("snapshot")))
    pool._model_clients["test"] = {"client": client(), "access_tier": "standard"}
    with model_budget_scope(uuid4(), uuid4(), "invalid"):
        with pytest.raises(ModelRequestInvalid):
            await pool.generate(transcript()[:3], model_selector="test")
    assert touched == []


async def test_actual_http_body_has_valid_tool_history_after_first_response(monkeypatch):
    requests = []
    def respond(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            message = {"content": "", "tool_calls": [{"id": "x", "type": "function",
                       "function": {"name": "list_run_candidates", "arguments": "{}"}}]}
            finish = "tool_calls"
        else:
            call = body["messages"][1]["tool_calls"][0]
            assert call["type"] == "function"
            assert json.loads(call["function"]["arguments"]) == {}
            assert body["messages"][2]["tool_call_id"] == call["id"]
            message, finish = {"content": "Files checked"}, "stop"
        return httpx.Response(200, json={"choices": [{"message": message, "finish_reason": finish}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    model = client()
    first = await model.generate([{"role": "user", "content": "Check files"}])
    call = first.tool_calls[0]
    second = await model.generate([
        {"role": "user", "content": "Check files"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": call.id, "name": call.name, "arguments": call.arguments}]},
        {"role": "tool", "tool_call_id": call.id, "content": "[]"},
    ])
    assert second.content == "Files checked"


def test_failure_classification_keeps_original_cause_without_message():
    cause = httpx.ReadTimeout("private response")
    wrapped = RuntimeError("private wrapper")
    wrapped.__cause__ = cause
    assert classify_model_failure(wrapped).code == "model_read_timeout"
    failure = classify_model_failure(ModelRequestInvalid("private arguments"))
    assert not failure.retryable
    assert "private" not in failure.safe_message


@pytest.mark.parametrize("api_format", ["openai", "anthropic"])
def test_consecutive_tool_round_trips_remain_distinct(api_format):
    messages = transcript()[:5] + [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c", "name": "read_file", "arguments": {"file": "next.txt"}}]},
        {"role": "tool", "tool_call_id": "c", "content": "next contents"},
    ]
    body = client(api_format).prepare_request(messages).body()
    if api_format == "openai":
        assert body["messages"][5]["tool_calls"][0]["function"]["name"] == "read_file"
        assert body["messages"][6]["tool_call_id"] == "c"
    else:
        assert body["messages"][3]["content"][0]["id"] == "c"
        assert body["messages"][4]["content"][0]["tool_use_id"] == "c"


async def test_malformed_provider_response_has_a_safe_distinct_category(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"error": "secret"})), **kw))
    with pytest.raises(Exception) as caught:
        await client().generate([{"role": "user", "content": "test"}])
    failure = classify_model_failure(caught.value)
    assert failure.code == "model_response_invalid" and not failure.retryable
    assert "secret" not in failure.safe_message


async def test_anthropic_system_prompt_is_included_in_budget_reservation():
    class CapturedReservation(Exception):
        pass
    amounts = []
    def reserve(*args, **kwargs):
        amounts.append(args[4])
        raise CapturedReservation()
    entitlement = SimpleNamespace(model_access_tier="standard", version=1)
    pool = ResourcePool(None,
        entitlement_service=SimpleNamespace(get=lambda _: SimpleNamespace(state=SimpleNamespace(value="valid"), entitlement=entitlement)),
        budget_coordinator=SimpleNamespace(reserve=reserve),
        snapshot_repository=SimpleNamespace(freeze=lambda **kwargs: SimpleNamespace(snapshot_id=uuid4())))
    pool._model_clients["test"] = {"client": client("anthropic"), "access_tier": "standard"}
    with model_budget_scope(uuid4(), uuid4(), "system-reservation"), pytest.raises(CapturedReservation):
        await pool.generate([{"role": "system", "content": "资料" * 250}, {"role": "user", "content": "hi"}], model_selector="test")
    assert amounts[0]["model_input_tokens"] >= 500
