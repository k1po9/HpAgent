"""Durable, declarative File Assistant output tools."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Literal

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from file_adapters import DocxWriter, GotenbergConversionProvider, PptxWriter, XlsxWriter
from file_runtime import FileResourceResolver, OutputPublisher


class DocxBlock(BaseModel):
    kind: Literal["heading", "paragraph"] = "paragraph"
    text: str = Field(max_length=20_000)
    level: int = Field(default=1, ge=1, le=9)


class CreateDocxInput(BaseModel):
    output_name: str = Field(min_length=1, max_length=255)
    blocks: list[DocxBlock] = Field(min_length=1, max_length=200)
    operation_id: str = Field(default="", max_length=200)


class WorkbookSheet(BaseModel):
    name: str = Field(min_length=1, max_length=31)
    rows: list[list[str | int | float | bool | None]] = Field(max_length=2000)


class CreateWorkbookInput(BaseModel):
    output_name: str = Field(min_length=1, max_length=255)
    sheets: list[WorkbookSheet] = Field(min_length=1, max_length=10)
    operation_id: str = Field(default="", max_length=200)


class PresentationSlide(BaseModel):
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20_000)


class CreatePresentationInput(BaseModel):
    output_name: str = Field(min_length=1, max_length=255)
    slides: list[PresentationSlide] = Field(min_length=1, max_length=100)
    operation_id: str = Field(default="", max_length=200)


class ConvertPdfInput(BaseModel):
    file: str
    output_name: str = Field(min_length=1, max_length=255)
    operation_id: str = Field(default="", max_length=200)


class ReplaceDocxTextInput(BaseModel):
    file: str
    output_name: str = Field(min_length=1, max_length=255)
    find: str = Field(min_length=1, max_length=10_000)
    replace: str = Field(max_length=20_000)
    max_replacements: int = Field(default=100, ge=1, le=1000)
    operation_id: str = Field(default="", max_length=200)


class AppendDocxSectionInput(BaseModel):
    file: str
    output_name: str = Field(min_length=1, max_length=255)
    heading: str = Field(default="", max_length=500)
    paragraphs: list[str] = Field(min_length=1, max_length=100)
    operation_id: str = Field(default="", max_length=200)


class WriteSheetRangeInput(BaseModel):
    file: str
    output_name: str = Field(min_length=1, max_length=255)
    sheet: str = Field(min_length=1, max_length=31)
    start_row: int = Field(ge=1, le=1_048_576)
    start_column: int = Field(ge=1, le=16_384)
    values: list[list[str | int | float | bool | None]] = Field(min_length=1, max_length=2000)
    operation_id: str = Field(default="", max_length=200)


class ReplaceSlideInput(BaseModel):
    file: str
    output_name: str = Field(min_length=1, max_length=255)
    slide: int = Field(ge=1)
    title: str = Field(default="", max_length=500)
    body: str = Field(default="", max_length=20_000)
    operation_id: str = Field(default="", max_length=200)


def _result(output: Any, *, scanned_bytes: int = 0) -> str:
    payload = {
        "file_id": str(output.file_id), "file": output.logical_name,
        "content_type": output.content_type, "sha256": output.sha256,
        "size_bytes": output.size_bytes, "scanned_bytes": scanned_bytes,
        "parent_file_id": (
            str(output.parent_file_id) if output.parent_file_id is not None else None
        ),
        "version": output.version,
        "written_bytes": output.size_bytes, "output_file_bytes": output.size_bytes,
        "returned_bytes": 0, "deduplicated": output.deduplicated,
    }
    while True:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        size = len(encoded.encode("utf-8"))
        if payload["returned_bytes"] == size:
            return encoded
        payload["returned_bytes"] = size


def create_file_write_tools(
    scope_provider: Callable[[], Any | None],
    publisher: OutputPublisher,
    conversion_provider: GotenbergConversionProvider | None = None,
) -> list[StructuredTool]:
    def active_scope():
        current = scope_provider()
        if current is None:
            raise ValueError("Run file scope is unavailable")
        return current

    async def create_docx(output_name: str, blocks: list[DocxBlock], operation_id: str = "") -> str:
        current = active_scope()
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name
        )
        if replayed is not None:
            return _result(replayed)
        data = [block.model_dump() for block in blocks]
        if sum(len(block["text"]) for block in data) > 200_000:
            raise ValueError("DOCX content exceeds bounded character limit")
        await asyncio.to_thread(DocxWriter().create, current.outputs_root, output_name, data)
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        return _result(output)

    async def create_workbook(
        output_name: str, sheets: list[WorkbookSheet], operation_id: str = ""
    ) -> str:
        current = active_scope()
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name
        )
        if replayed is not None:
            return _result(replayed)
        data = [sheet.model_dump() for sheet in sheets]
        if sum(len(row) for sheet in data for row in sheet["rows"]) > 20_000:
            raise ValueError("workbook exceeds bounded cell limit")
        if any(len(row) > 100 for sheet in data for row in sheet["rows"]):
            raise ValueError("workbook row exceeds bounded column limit")
        await asyncio.to_thread(XlsxWriter().create, current.outputs_root, output_name, data)
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        return _result(output)

    async def create_presentation(
        output_name: str, slides: list[PresentationSlide], operation_id: str = ""
    ) -> str:
        current = active_scope()
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name
        )
        if replayed is not None:
            return _result(replayed)
        data = [slide.model_dump() for slide in slides]
        await asyncio.to_thread(PptxWriter().create, current.outputs_root, output_name, data)
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        return _result(output)

    async def convert_file_to_pdf(file: str, output_name: str, operation_id: str = "") -> str:
        if conversion_provider is None:
            raise RuntimeError("Gotenberg conversion is unavailable")
        current = active_scope()
        resource = FileResourceResolver(current).resolve(file)
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name, resource.file_id
        )
        if replayed is not None:
            return _result(replayed, scanned_bytes=resource.size_bytes)
        await asyncio.to_thread(
            conversion_provider.convert_to_pdf, resource, current.outputs_root, output_name
        )
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name, "application/pdf",
            parent_file_id=resource.file_id,
        )
        return _result(output, scanned_bytes=resource.size_bytes)

    async def replace_docx_text(
        file: str, output_name: str, find: str, replace: str,
        max_replacements: int = 100, operation_id: str = "",
    ) -> str:
        current = active_scope()
        resource = FileResourceResolver(current).resolve(file)
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name, resource.file_id
        )
        if replayed is not None:
            return _result(replayed, scanned_bytes=resource.size_bytes)
        _, replacements = await asyncio.to_thread(
            DocxWriter().replace_text, resource, current.outputs_root, output_name,
            find, replace, max_replacements,
        )
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            parent_file_id=resource.file_id,
        )
        payload = json.loads(_result(output, scanned_bytes=resource.size_bytes))
        payload["replacements"] = replacements
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    async def append_docx_section(
        file: str, output_name: str, heading: str, paragraphs: list[str],
        operation_id: str = "",
    ) -> str:
        if sum(map(len, paragraphs)) > 200_000:
            raise ValueError("DOCX section exceeds bounded character limit")
        current = active_scope()
        resource = FileResourceResolver(current).resolve(file)
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name, resource.file_id
        )
        if replayed is not None:
            return _result(replayed, scanned_bytes=resource.size_bytes)
        await asyncio.to_thread(
            DocxWriter().append_section, resource, current.outputs_root, output_name,
            heading, paragraphs,
        )
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            parent_file_id=resource.file_id,
        )
        return _result(output, scanned_bytes=resource.size_bytes)

    async def write_sheet_range(
        file: str, output_name: str, sheet: str, start_row: int, start_column: int,
        values: list[list[str | int | float | bool | None]], operation_id: str = "",
    ) -> str:
        if sum(len(row) for row in values) > 20_000 or any(len(row) > 100 for row in values):
            raise ValueError("workbook patch exceeds bounded cell limit")
        current = active_scope()
        resource = FileResourceResolver(current).resolve(file)
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name, resource.file_id
        )
        if replayed is not None:
            return _result(replayed, scanned_bytes=resource.size_bytes)
        await asyncio.to_thread(
            XlsxWriter().write_range, resource, current.outputs_root, output_name,
            sheet, start_row, start_column, values,
        )
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            parent_file_id=resource.file_id,
        )
        return _result(output, scanned_bytes=resource.size_bytes)

    async def replace_slide(
        file: str, output_name: str, slide: int, title: str = "", body: str = "",
        operation_id: str = "",
    ) -> str:
        current = active_scope()
        resource = FileResourceResolver(current).resolve(file)
        replayed = await asyncio.to_thread(
            publisher.replay, current, operation_id, output_name, resource.file_id
        )
        if replayed is not None:
            return _result(replayed, scanned_bytes=resource.size_bytes)
        await asyncio.to_thread(
            PptxWriter().replace_slide, resource, current.outputs_root, output_name,
            slide, title, body,
        )
        output = await asyncio.to_thread(
            publisher.publish, current, operation_id, output_name,
            "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            parent_file_id=resource.file_id,
        )
        return _result(output, scanned_bytes=resource.size_bytes)

    definitions = [
        ("create_docx", "Create and publish a bounded DOCX output.", CreateDocxInput, create_docx),
        ("create_workbook", "Create and publish a bounded XLSX output.", CreateWorkbookInput, create_workbook),
        ("create_presentation", "Create and publish a bounded PPTX output.", CreatePresentationInput, create_presentation),
        ("convert_file_to_pdf", "Convert a Run Office input to PDF with Gotenberg.", ConvertPdfInput, convert_file_to_pdf),
        ("replace_docx_text", "Replace bounded literal text and publish a new DOCX version.", ReplaceDocxTextInput, replace_docx_text),
        ("append_docx_section", "Append a bounded section and publish a new DOCX version.", AppendDocxSectionInput, append_docx_section),
        ("write_sheet_range", "Write a bounded XLSX range and publish a new version.", WriteSheetRangeInput, write_sheet_range),
        ("replace_slide", "Replace one PPTX slide and publish a new version.", ReplaceSlideInput, replace_slide),
    ]
    tools = [
        StructuredTool.from_function(
            name=name, description=description, args_schema=schema, coroutine=coroutine
        )
        for name, description, schema, coroutine in definitions
    ]
    for tool in tools:
        tool.metadata = {
            "side_effect_class": "idempotent_write",
            "idempotency_key_argument": "operation_id",
            "file_scope_required": True,
            "budget_reservation": {
                "tool_calls": 1, "bytes_scanned": 128 * 1024 * 1024,
                "bytes_written": 128 * 1024 * 1024,
                "output_file_bytes": 128 * 1024 * 1024,
                "bytes_returned_to_model": 4096,
            },
            "usage_json_fields": {
                "scanned_bytes": "bytes_scanned", "written_bytes": "bytes_written",
                "output_file_bytes": "output_file_bytes",
                "returned_bytes": "bytes_returned_to_model",
            },
            "trace_json_fields": {"deduplicated": "deduplicated", "size_bytes": "output_bytes"},
        }
    return tools
