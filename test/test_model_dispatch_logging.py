from __future__ import annotations

import logging

import httpx
import pytest

from resources.model_client import ModelClient, ModelDispatchError


@pytest.mark.parametrize("error_type", [httpx.ConnectTimeout, httpx.ReadTimeout])
async def test_dispatch_logs_timeout_category_without_sensitive_content(monkeypatch, caplog, error_type):
    class Transport:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, *args, **kwargs):
            raise error_type("SECRET_RESPONSE_SENTINEL")

    monkeypatch.setattr(httpx, "AsyncClient", Transport)
    client = ModelClient({"api_key": "SECRET_KEY_SENTINEL", "base_url": "https://example.test/v1",
                          "model": "test", "provider": "test", "endpoint_id": "test-endpoint"})
    with caplog.at_level(logging.WARNING, logger="HpAgent.ModelClient"):
        with pytest.raises(ModelDispatchError):
            await client.generate([{"role": "user", "content": "SECRET_PROMPT_SENTINEL"}])
    record = next(r for r in caplog.records if r.getMessage() == "model_dispatch_failed")
    assert record.error_category == error_type.__name__
    assert record.endpoint_id == "test-endpoint"
    assert record.elapsed_ms >= 0
    assert "SECRET_" not in caplog.text
    assert "SECRET_" not in repr(vars(record))


async def test_http_failure_logs_bounded_provider_identity_and_call_context(monkeypatch, caplog):
    from uuid import uuid4

    from resources.model_budget_context import model_budget_scope
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(
        lambda _: httpx.Response(500, headers={"x-request-id": "provider-123"},
                                json={"error": {"code": "invalid_request", "message": "SECRET_PROMPT"}})), **kw))
    client = ModelClient({"api_key": "SECRET_KEY", "base_url": "https://example.test", "model": "test", "api_format": "openai"})
    run_id = uuid4()
    with model_budget_scope(uuid4(), run_id, "operation", phase="model", read_timeout_seconds=90) as context:
        context.snapshot_id = "snapshot-123"
        context.attempt = 2
        with caplog.at_level(logging.WARNING, logger="HpAgent.ModelClient"), pytest.raises(ModelDispatchError):
            await client.generate([{"role": "user", "content": "SECRET_PROMPT"}])
    record = next(r for r in caplog.records if r.getMessage() == "model_http_error")
    assert record.http_status == 500 and record.provider_error_code == "invalid_request"
    assert record.provider_request_id == "provider-123" and record.run_id == str(run_id)
    assert record.snapshot_id == "snapshot-123" and record.attempt == 2
    assert "SECRET" not in repr(vars(record))


async def test_artifact_read_budget_does_not_extend_connection_or_other_calls(monkeypatch):
    from uuid import uuid4

    from resources.model_budget_context import model_budget_scope
    observed = []
    original = httpx.AsyncClient
    def transport(**kwargs):
        observed.append(kwargs["timeout"])
        return original(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})), **kwargs)
    monkeypatch.setattr(httpx, "AsyncClient", transport)
    client = ModelClient({"api_key": "offline", "base_url": "https://example.test", "model": "test", "api_format": "openai", "timeout": 30})
    with model_budget_scope(uuid4(), uuid4(), "artifact", read_timeout_seconds=90):
        await client.generate([{"role": "user", "content": "test"}])
    await client.generate([{"role": "user", "content": "test"}])
    assert [(t.connect, t.read) for t in observed] == [(5, 90), (5, 30)]
