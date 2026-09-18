from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest

from account.entitlement_service import AccountEntitlement, EntitlementLookup, EntitlementState
from common.types import ModelResponse
from resources.model_budget_context import model_budget_scope
from resources.model_client import ModelClient, ModelDispatchError
from resources.model_input_snapshot import snapshot_content_hash
from resources.resource_pool import ResourcePool
from tracing.metadata import sanitize_trace_metadata


def _client(endpoint: str = "primary", *, api_format: str = "openai") -> ModelClient:
    return ModelClient({
        "endpoint_id": endpoint, "provider": "provider", "api_key": "SECRET_SENTINEL",
        "base_url": "https://example.test/v1", "model": f"model-{endpoint}",
        "api_format": api_format, "max_tokens": 41,
        "extra_body": {"temperature": 0.2},
    })


def test_prepared_requests_preserve_provider_conversion_defaults_and_are_secret_free() -> None:
    tools = [{"name": "lookup", "description": "x", "input_schema": {"type": "object"}}]
    openai = _client().prepare_request(
        [{"role": "user", "content": "hello"}], tools, False, None
    )
    assert openai.body()["tools"][0]["type"] == "function"
    assert openai.body()["max_tokens"] == 41
    assert openai.body()["temperature"] == 0.2
    anthropic = _client(api_format="anthropic").prepare_request(
        [{"role": "user", "content": "hello"}], tools, False, 17
    )
    assert anthropic.body()["tools"][0]["input_schema"] == {"type": "object"}
    assert anthropic.body()["max_tokens"] == 17
    assert "SECRET_SENTINEL" not in repr(openai)
    assert not hasattr(openai, "headers")


def test_semantic_hash_is_stable_and_binds_request_identity() -> None:
    original = _client("e").prepare_request([], max_tokens=5)
    assert snapshot_content_hash(original) == snapshot_content_hash(original)
    assert snapshot_content_hash(original) != snapshot_content_hash(
        _client("other").prepare_request([], max_tokens=5)
    )
    assert snapshot_content_hash(original) != snapshot_content_hash(
        _client("e").prepare_request([], max_tokens=6)
    )


class _Entitlements:
    def __init__(self, tier: str = "standard"):
        self.tier = tier

    def get(self, account_id):
        return EntitlementLookup(EntitlementState.VALID, AccountEntitlement(
            account_id, self.tier, None, "none", None, 3, None,
        ))


class _Snapshots:
    def __init__(self):
        self.items = []

    def freeze(self, **kwargs):
        self.items.append(kwargs)
        return SimpleNamespace(snapshot_id=uuid4(), content_hash=b"\xab" * 32)


class _Budget:
    def __init__(self, *, deny: bool = False):
        self.calls = []
        self.deny = deny

    def reserve(self, *args, **kwargs):
        self.calls.append(("reserve", args, kwargs))
        if self.deny:
            raise RuntimeError("quota denied")
        return SimpleNamespace(account=SimpleNamespace(
            quota_date=date(2026, 9, 18), replayed=False, state="reserved",
        ))

    def settle(self, *args, **kwargs):
        self.calls.append(("settle", args, kwargs))

    def release(self, *args, **kwargs):
        self.calls.append(("release", args, kwargs))


class _Transport:
    def __init__(self, client: ModelClient, outcome):
        self.client = client
        self.outcome = outcome
        self.dispatched = []
        self.prepared = 0

    def prepare_request(self, *args, **kwargs):
        self.prepared += 1
        return self.client.prepare_request(*args, **kwargs)

    async def send_prepared(self, prepared):
        self.dispatched.append(prepared.body())
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def __getattr__(self, name):
        return getattr(self.client, name)


def _pool(transports, *, tier="standard", budget=None):
    snapshots = _Snapshots()
    budget = budget or _Budget()
    pool = ResourcePool(
        SimpleNamespace(), entitlement_service=_Entitlements(tier),
        budget_coordinator=budget, snapshot_repository=snapshots,
    )
    pool._model_clients = {
        name: {"client": transport, "access_tier": "standard"}
        for name, transport in transports.items()
    }
    pool._fallback_groups = {"chat": list(transports)}
    return pool, snapshots, budget


@pytest.mark.asyncio
async def test_fallback_groups_one_logical_call_and_conservatively_settles_uncertain_attempt(monkeypatch):
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr("resources.resource_pool.asyncio.to_thread", direct)
    first = _Transport(_client("first"), ModelDispatchError("timeout"))
    second = _Transport(_client("second"), ModelResponse(
        content="ok", usage={"input_tokens": 2, "output_tokens": 1,
                             "total_tokens": 3, "usage_source": "provider"},
    ))
    pool, snapshots, budget = _pool({"first": first, "second": second})
    account_id, run_id = uuid4(), uuid4()
    with model_budget_scope(account_id, run_id, "decision", phase="decision"):
        result = await pool.generate([{"role": "user", "content": "hello"}], "chat")
    assert result.content == "ok"
    assert len(snapshots.items) == 2
    assert len({item["model_call_id"] for item in snapshots.items}) == 1
    assert [item["fallback_attempt"] for item in snapshots.items] == [1, 2]
    assert snapshots.items[0]["endpoint_id"] != snapshots.items[1]["endpoint_id"]
    settlements = [item for item in budget.calls if item[0] == "settle"]
    assert settlements[0][1][-1] == "estimated"
    assert settlements[1][1][-1] == "provider"


@pytest.mark.asyncio
async def test_disallowed_tier_and_quota_denial_never_dispatch(monkeypatch):
    async def direct(function, *args, **kwargs):
        return function(*args, **kwargs)
    monkeypatch.setattr("resources.resource_pool.asyncio.to_thread", direct)
    denied = _Transport(_client(), ModelResponse(content="bad"))
    pool, snapshots, _budget = _pool({"primary": denied}, tier="basic")
    with model_budget_scope(uuid4(), uuid4(), "tier"):
        with pytest.raises(Exception):
            await pool.generate([{"role": "user", "content": "hello"}], "chat")
    assert denied.prepared == 0 and denied.dispatched == [] and snapshots.items == []

    quota_transport = _Transport(_client(), ModelResponse(content="bad"))
    quota = _Budget(deny=True)
    pool, snapshots, _ = _pool({"primary": quota_transport}, budget=quota)
    with model_budget_scope(uuid4(), uuid4(), "quota"):
        with pytest.raises(RuntimeError, match="quota denied"):
            await pool.generate([{"role": "user", "content": "hello"}], "chat")
    assert len(snapshots.items) == 1
    assert quota_transport.dispatched == []


def test_trace_accepts_snapshot_refs_and_rejects_prompt_or_secret_fields() -> None:
    safe = sanitize_trace_metadata("LLMCall", {
        "snapshot_id": str(uuid4()), "model_call_id": str(uuid4()),
        "content_hash": "ab" * 32, "provider_outcome": "uncertain",
        "provider_request_body": {"messages": ["SECRET_SENTINEL"]},
        "messages": ["SECRET_SENTINEL"], "api_key": "SECRET_SENTINEL",
    })
    assert safe["provider_outcome"] == "uncertain"
    assert "SECRET_SENTINEL" not in repr(safe)
