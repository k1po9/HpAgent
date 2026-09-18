from __future__ import annotations

from dataclasses import fields

import pytest

from resources.model_client import ModelClient, PreparedModelRequest
from resources.model_input_snapshot import snapshot_content_hash
from tracing.metadata import sanitize_trace_metadata


@pytest.mark.parametrize("api_format", ["anthropic", "openai"])
def test_prepared_request_freezes_effective_provider_body(api_format: str) -> None:
    client = ModelClient({
        "api_key": "SENTINEL-SECRET", "base_url": "https://example.test/v1",
        "model": "model-a", "endpoint_id": "chat:0", "provider": "example",
        "api_format": api_format, "max_tokens": 77,
        "extra_body": {"temperature": 0.25},
    })
    prepared = client.prepare_request(
        [{"role": "user", "content": "hello"}],
        [{"name": "lookup", "description": "x", "input_schema": {"type": "object"}}],
        False,
    )
    body = prepared.body()
    assert body["model"] == "model-a"
    assert body["max_tokens"] == 77
    assert body["temperature"] == 0.25
    assert "SENTINEL-SECRET" not in repr(prepared)
    assert "api_key" not in {item.name for item in fields(PreparedModelRequest)}
    with pytest.raises(TypeError):
        prepared.payload["model"] = "changed"  # type: ignore[index]


def test_snapshot_hash_is_stable_and_binds_request_and_endpoint() -> None:
    def prepared(endpoint: str, model: str, content: str):
        return ModelClient({
            "api_key": "secret", "base_url": "https://example.test/v1",
            "model": model, "endpoint_id": endpoint, "provider": "p",
        }).prepare_request([{"role": "user", "content": content}])

    first = prepared("a", "m", "same")
    assert snapshot_content_hash(first) == snapshot_content_hash(first)
    assert snapshot_content_hash(first) != snapshot_content_hash(prepared("a", "m", "changed"))
    assert snapshot_content_hash(first) != snapshot_content_hash(prepared("b", "m", "same"))
    assert snapshot_content_hash(first) != snapshot_content_hash(prepared("a", "m2", "same"))


def test_trace_metadata_retains_refs_and_rejects_prompts_and_secrets() -> None:
    safe = sanitize_trace_metadata("LLMCall", {
        "model_call_id": "call", "snapshot_id": "snapshot",
        "content_hash": "abc", "fallback_attempt": 2,
        "provider_outcome": "uncertain", "messages": "PROMPT",
        "api_key": "SENTINEL-SECRET", "headers": "SENTINEL-SECRET",
    })
    assert safe["snapshot_id"] == "snapshot"
    assert safe["provider_outcome"] == "uncertain"
    assert "messages" not in safe and "api_key" not in safe and "headers" not in safe
    assert "SENTINEL-SECRET" not in repr(safe)
