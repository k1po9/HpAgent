from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import uuid4

import pytest

from orchestration.artifact_dispatcher import ArtifactOutboxDispatcher, artifact_workflow_id
from web_artifacts.generator import ArtifactGenerationError, WebArtifactGenerator


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
