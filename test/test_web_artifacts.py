from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest

from common.errors import ModelAPIError
from orchestration.artifact_dispatcher import ArtifactOutboxDispatcher, artifact_workflow_id
from resources.account_daily_budget import AccountDailyBudgetExhausted
from resources.model_budget_context import model_budget_scope
from resources.model_client import ModelClient, ModelDispatchError
from resources.model_governance_errors import ModelAccessTierDenied
from resources.resource_pool import ResourcePool
from web_artifacts.build import ArtifactBuildService
from web_artifacts.generator import (
    ArtifactGenerationError,
    WebArtifactGenerator,
    classify_model_failure,
)


@dataclass
class _Response:
    content: str


class _Model:
    def __init__(self, content: str):
        self.content = content

    async def generate(self, **_kwargs):
        return _Response(self.content)


@pytest.mark.asyncio
async def test_generator_strips_fence_and_injects_csp_and_error_bridge():
    generator = WebArtifactGenerator(
        _Model("```html\n<!doctype html><html><head><title>x</title></head><body>x</body></html>\n```")
    )
    html = await generator.generate(source_markdown="# X", instruction=None)
    assert not html.startswith("```")
    assert "Content-Security-Policy" in html
    assert "connect-src 'none'" in html
    assert "hpagent-artifact-runtime-error" in html


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["", "<div>fragment</div>"])
async def test_generator_rejects_incomplete_html(value: str):
    with pytest.raises(ArtifactGenerationError) as error:
        await WebArtifactGenerator(_Model(value)).generate(
            source_markdown="# X", instruction=None
        )
    assert error.value.code == "artifact_invalid_html"


@pytest.mark.asyncio
async def test_generator_rejects_oversized_hardened_html():
    model = _Model("<html><head></head><body>1234567890</body></html>")
    with pytest.raises(ArtifactGenerationError) as error:
        await WebArtifactGenerator(model, max_bytes=100).generate(
            source_markdown="# X", instruction=None
        )
    assert error.value.code == "artifact_html_too_large"


@pytest.mark.parametrize(("underlying", "code", "retryable"), [
    (TimeoutError("secret timeout"), "artifact_model_timeout", True),
    (ConnectionError("secret connection"), "artifact_model_connection_failed", True),
    (ModelAccessTierDenied("secret"), "model_access_tier_denied", False),
    (AccountDailyBudgetExhausted("secret"), "account_daily_model_budget_exhausted", False),
    (RuntimeError("secret internal"), "artifact_internal_error", False),
])
@pytest.mark.asyncio
async def test_generator_classifies_and_redacts_model_failure(underlying, code, retryable):
    class FailingModel:
        async def generate(self, **_kwargs):
            raise underlying

    with pytest.raises(ArtifactGenerationError) as error:
        await WebArtifactGenerator(FailingModel()).generate(source_markdown="# X", instruction=None)
    assert error.value.code == code
    assert error.value.retryable is retryable
    assert "secret" not in error.value.safe_message
    assert "secret" not in str(error.value)


@pytest.mark.parametrize(("status", "code"), [
    (400, "artifact_model_request_rejected"),
    (401, "artifact_model_access_denied"),
    (403, "artifact_model_access_denied"),
    (429, "artifact_model_quota_exhausted"),
    (503, "artifact_model_http_error"),
])
def test_classifies_http_status_through_resource_pool_chain(status, code):
    request = httpx.Request("POST", "https://example.invalid/model")
    response = httpx.Response(status, request=request, text="secret provider response")
    try:
        try:
            raise httpx.HTTPStatusError("secret", request=request, response=response)
        except httpx.HTTPStatusError as cause:
            raise ModelDispatchError("HTTP failure", status_code=status) from cause
    except ModelDispatchError as dispatched:
        try:
            raise ModelAPIError("all models failed") from dispatched
        except ModelAPIError as pooled:
            failure = classify_model_failure(pooled)
    assert failure.code == code
    assert failure.exception_type == "HTTPStatusError"
    assert "secret" not in failure.safe_message


def test_classifies_timeout_and_connection_through_resource_pool_chain():
    for original, code in [
        (httpx.ReadTimeout("secret"), "artifact_model_timeout"),
        (httpx.ConnectError("secret"), "artifact_model_connection_failed"),
    ]:
        try:
            try:
                raise original
            except httpx.RequestError as cause:
                raise ModelDispatchError("dispatch") from cause
        except ModelDispatchError as dispatched:
            try:
                raise ModelAPIError("pool") from dispatched
            except ModelAPIError as pooled:
                assert classify_model_failure(pooled).code == code


def test_unknown_exception_inside_model_wrappers_stays_internal():
    try:
        try:
            raise ValueError("secret malformed response")
        except ValueError as cause:
            raise ModelDispatchError("ValueError") from cause
    except ModelDispatchError as dispatched:
        try:
            raise ModelAPIError("pool") from dispatched
        except ModelAPIError as pooled:
            failure = classify_model_failure(pooled)
    assert failure.code == "artifact_internal_error"
    assert failure.exception_type == "ValueError"
    assert "secret" not in failure.safe_message


@pytest.mark.asyncio
async def test_model_client_redacts_provider_response_and_preserves_status(monkeypatch, caplog):
    secret = "sk-secret-provider-body"
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real_client(
        transport=httpx.MockTransport(lambda request: httpx.Response(
            503, request=request, text=secret
        )), **kwargs,
    ))
    client = ModelClient({"api_key": "sk-private", "base_url": "https://example.invalid",
                          "model": "test", "provider": "test", "api_format": "openai"})
    with caplog.at_level("DEBUG"), pytest.raises(ModelDispatchError) as error:
        await client.generate(messages=[{"role": "user", "content": "private prompt"}])
    assert error.value.details["status_code"] == 503
    assert isinstance(error.value.__cause__, httpx.HTTPStatusError)
    assert secret not in caplog.text
    assert secret not in str(error.value)
    assert "sk-private" not in caplog.text
    assert "private prompt" not in caplog.text


@pytest.mark.asyncio
async def test_resource_pool_chain_and_structured_artifact_call_events(caplog):
    class FailingClient:
        provider = "test-provider"
        model = "test-model"

        async def generate(self, **_kwargs):
            raise ModelDispatchError("HTTP 503", status_code=503)

    pool = ResourcePool(None)
    pool._model_clients["endpoint-1"] = {"client": FailingClient()}
    pool.configure_fallback_group("chat", ["endpoint-1"])
    with caplog.at_level(logging.INFO):
        with model_budget_scope(uuid4(), uuid4(), "artifact-test", phase="artifact_generation",
                                artifact_id="artifact-1", artifact_version_id="version-1") as context:
            with pytest.raises(ModelAPIError) as error:
                await pool.generate(messages=[{"role": "user", "content": "secret prompt"}],
                                    model_selector="chat")
    assert isinstance(error.value.__cause__, ModelDispatchError)
    assert classify_model_failure(error.value).code == "artifact_model_http_error"
    records = [record for record in caplog.records
               if record.__dict__.get("event", "").startswith("artifact_model_call_")]
    assert [record.event for record in records] == [
        "artifact_model_call_started", "artifact_model_call_failed",
    ]
    assert {record.model_call_id for record in records} == {str(context.model_call_id)}
    assert records[1].endpoint_id == "endpoint-1"
    assert records[1].provider == "test-provider"
    assert records[1].model == "test-model"
    assert records[1].error_code == "artifact_model_http_error"
    assert records[1].attempt == 1
    assert "secret prompt" not in caplog.text


def test_artifact_failure_update_uses_stable_code_and_safe_message(monkeypatch):
    statements = []

    class FakeUow:
        def __init__(self, _database):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def execute(self, sql, values):
            statements.append((sql, values))

    monkeypatch.setattr("web_artifacts.build.UnitOfWork", FakeUow)
    version_id = uuid4()
    ArtifactBuildService(object(), object())._fail(
        version_id, "artifact_model_timeout", "模型调用超时。",
    )
    sql, values = statements[0]
    assert "status='failed'" in sql
    assert "failure_code=%s" in sql and "failure_message=%s" in sql
    assert values == ("artifact_model_timeout", "模型调用超时。", version_id)


class _Outbox:
    def __init__(self, version_id):
        self.version_id = version_id
        self.processed = False
        self.failed = False

    def claim(self, _worker_id, _limit):
        return [{"artifact_outbox_event_id": uuid4(), "artifact_version_id": self.version_id,
                 "attempt_count": 1}]

    def mark_processed(self, *_args):
        self.processed = True

    def dead_letter(self, *_args):
        return True

    def fail_version(self, *_args):
        self.failed = True


class _Temporal:
    def __init__(self):
        self.started = []

    async def start(self, version_id):
        self.started.append(version_id)


class _FailingTemporal:
    async def start(self, _version_id):
        raise RuntimeError("temporal unavailable")


@pytest.mark.asyncio
async def test_artifact_dispatcher_uses_version_as_deterministic_identity(monkeypatch):
    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", immediate)
    version_id = uuid4()
    outbox, temporal = _Outbox(version_id), _Temporal()
    dispatcher = ArtifactOutboxDispatcher(outbox, temporal, "worker")
    assert await dispatcher.run_once() == 1
    assert temporal.started == [version_id]
    assert outbox.processed is True
    assert artifact_workflow_id(version_id) == f"hpagent-web-artifact-{version_id}"


@pytest.mark.asyncio
async def test_dispatch_exhaustion_dead_letters_and_fails_version(monkeypatch):
    async def immediate(function, *args):
        return function(*args)

    monkeypatch.setattr(asyncio, "to_thread", immediate)
    version_id = uuid4()
    outbox = _Outbox(version_id)
    dispatcher = ArtifactOutboxDispatcher(outbox, _FailingTemporal(), "worker", max_attempts=1)
    assert await dispatcher.run_once() == 1
    assert outbox.failed is True
