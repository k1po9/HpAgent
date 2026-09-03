from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from file_adapters import GotenbergConversionProvider
from file_domain.models import FileResource
from file_runtime import PublishedOutput
from sandbox.tools.local.file_write import create_file_write_tools
from workspace.file_scope import RunFileScope


class _Publisher:
    def replay(self, scope, operation_id, logical_name, parent_file_id=None):
        return None

    def publish(
        self, scope, operation_id, logical_name, content_type, parent_file_id=None
    ):
        path = scope.outputs_root / logical_name
        return PublishedOutput(
            uuid4(), logical_name, content_type, path.stat().st_size, "a" * 64,
            parent_file_id,
        )


def _scope(tmp_path: Path) -> RunFileScope:
    for name in ("inputs", "scratch", "outputs"):
        (tmp_path / name).mkdir()
    return RunFileScope(uuid4(), tmp_path / "inputs", tmp_path / "scratch", tmp_path / "outputs", ())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "suffix"),
    [
        ("create_docx", {"output_name": "report.docx", "blocks": [{"kind": "heading", "text": "Title"}]}, ".docx"),
        ("create_workbook", {"output_name": "data.xlsx", "sheets": [{"name": "Data", "rows": [["a", 1]]}]}, ".xlsx"),
        ("create_presentation", {"output_name": "deck.pptx", "slides": [{"title": "T", "body": "B"}]}, ".pptx"),
    ],
)
async def test_office_output_tools_create_and_publish(tmp_path, name, arguments, suffix):
    scope = _scope(tmp_path)
    tools = {tool.name: tool for tool in create_file_write_tools(lambda: scope, _Publisher())}
    arguments["operation_id"] = f"op:{name}"
    payload = json.loads(await tools[name].ainvoke(arguments))
    assert payload["file"].endswith(suffix)
    assert payload["written_bytes"] > 0
    assert (scope.outputs_root / payload["file"]).is_file()
    assert tools[name].metadata["side_effect_class"] == "idempotent_write"
    assert tools[name].metadata["idempotency_key_argument"] == "operation_id"


def test_gotenberg_provider_uses_libreoffice_and_writes_pdf(tmp_path):
    source = tmp_path / "input.docx"
    source.write_bytes(b"office")
    outputs = tmp_path / "outputs"
    outputs.mkdir()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/forms/libreoffice/convert"
        return httpx.Response(200, content=b"%PDF-1.7\nfixture")

    provider = GotenbergConversionProvider(
        "http://gotenberg:3000", transport=httpx.MockTransport(handler)
    )
    resource = FileResource(
        uuid4(), "input.docx", source, source.stat().st_size,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    result = provider.convert_to_pdf(resource, outputs, "result.pdf")
    assert result.read_bytes().startswith(b"%PDF-1.7")


def test_gotenberg_provider_rejects_unapproved_input(tmp_path):
    source = tmp_path / "input.html"
    source.write_text("<p>x</p>")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    resource = FileResource(uuid4(), "input.html", source, source.stat().st_size, "text/html")
    with pytest.raises(ValueError, match="accepts DOCX"):
        GotenbergConversionProvider("http://gotenberg").convert_to_pdf(
            resource, outputs, "result.pdf"
        )


def test_gotenberg_provider_removes_partial_output_when_response_exceeds_limit(tmp_path):
    source = tmp_path / "input.docx"
    source.write_bytes(b"office")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    provider = GotenbergConversionProvider(
        "http://gotenberg",
        max_output_bytes=4,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF-too-large")
        ),
    )
    resource = FileResource(uuid4(), "input.docx", source, 6, "application/docx")
    with pytest.raises(ValueError, match="exceeds output byte limit"):
        provider.convert_to_pdf(resource, outputs, "result.pdf")
    assert not list(outputs.iterdir())
