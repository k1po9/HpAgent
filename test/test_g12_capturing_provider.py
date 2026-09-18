from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from account.entitlement_service import AccountEntitlement, EntitlementLookup, EntitlementState
from resources.model_budget_context import model_budget_scope
from resources.model_client import ModelClient
from resources.model_input_snapshot import semantic_request_hash
from resources.resource_pool import ResourcePool


class _Entitlements:
    def get(self, account_id):
        return EntitlementLookup(
            EntitlementState.VALID,
            AccountEntitlement(account_id, "standard", None, "full_safe", None, 7, None),
        )


class _Snapshots:
    def __init__(self):
        self.items = []

    def freeze(self, **values):
        digest = semantic_request_hash(
            endpoint_id=values["endpoint_id"],
            provider=values["provider"],
            model=values["model"],
            api_format=values["api_format"],
            payload=values["payload"],
            serializer_version=values["serializer_version"],
        )
        item = SimpleNamespace(snapshot_id=uuid4(), content_hash=digest, **values)
        self.items.append(item)
        return item


class _Budget:
    def __init__(self):
        self.calls = []

    def reserve(self, *args, **kwargs):
        self.calls.append(("reserve", args, kwargs))
        return SimpleNamespace(
            account=SimpleNamespace(
                quota_date=date(2026, 9, 18), replayed=False, state="reserved"
            )
        )

    def settle(self, *args, **kwargs):
        self.calls.append(("settle", args, kwargs))

    def release(self, *args, **kwargs):
        self.calls.append(("release", args, kwargs))


class _Response:
    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class _CapturingAsyncClient:
    captures = []

    def __init__(self, **_kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, json, headers):
        self.captures.append({"url": url, "body": json, "headers": headers})
        if url.endswith("/messages"):
            return _Response({
                "content": [{"type": "text", "text": "ok"}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 2, "output_tokens": 1},
            })
        return _Response({
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2, "completion_tokens": 1, "total_tokens": 3},
        })


def _client(endpoint, api_format):
    return ModelClient({
        "endpoint_id": endpoint,
        "provider": "capturing-provider",
        "api_key": "G12-CREDENTIAL-MUST-NOT-PERSIST",
        "base_url": "https://capture.test/v1",
        "model": f"model-{endpoint}",
        "api_format": api_format,
        "max_tokens": 37,
        "extra_body": {"temperature": 0.15},
    })


@pytest.mark.asyncio
@pytest.mark.parametrize("api_format", ["anthropic", "openai"])
async def test_g12_captured_dispatch_body_equals_snapshot_and_hash(monkeypatch, api_format):
    """G12 captures the object passed to HTTP dispatch, not only _build_payload()."""
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)

    _CapturingAsyncClient.captures = []
    monkeypatch.setattr(httpx, "AsyncClient", _CapturingAsyncClient)
    monkeypatch.setattr("resources.resource_pool.asyncio.to_thread", direct)
    snapshots = _Snapshots()
    budget = _Budget()
    client = _client(f"{api_format}:primary", api_format)
    pool = ResourcePool(
        SimpleNamespace(),
        entitlement_service=_Entitlements(),
        budget_coordinator=budget,
        snapshot_repository=snapshots,
    )
    pool._model_clients = {
        client.endpoint_id: {"client": client, "access_tier": "standard"}
    }
    pool._fallback_groups = {"chat": [client.endpoint_id]}
    messages = [{"role": "user", "content": "G12-PROMPT-SENTINEL"}]
    tools = [{"name": "lookup", "description": "lookup", "input_schema": {"type": "object"}}]

    with model_budget_scope(uuid4(), uuid4(), "react-decision", phase="decision"):
        result = await pool.generate(messages, "chat", tools=tools)

    captured = _CapturingAsyncClient.captures[0]
    snapshot = snapshots.items[0]
    assert captured["body"] == snapshot.payload
    assert captured["body"]["max_tokens"] == 37
    assert captured["body"]["temperature"] == 0.15
    assert captured["body"]["tools"]
    assert snapshot.content_hash == semantic_request_hash(
        endpoint_id=snapshot.endpoint_id,
        provider=snapshot.provider,
        model=snapshot.model,
        api_format=snapshot.api_format,
        payload=captured["body"],
        serializer_version=snapshot.serializer_version,
    )
    assert result.snapshot_id == str(snapshot.snapshot_id)
    assert result.content_hash == snapshot.content_hash.hex()
    assert "G12-CREDENTIAL-MUST-NOT-PERSIST" not in repr(snapshot.payload)
    assert "G12-CREDENTIAL-MUST-NOT-PERSIST" not in repr(result)
    assert any(key.lower() in {"authorization", "x-api-key"} for key in captured["headers"])

