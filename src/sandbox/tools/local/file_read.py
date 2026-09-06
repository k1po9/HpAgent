"""Typed, bounded File Assistant read tools over the active RunFileScope."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from file_adapters import (
    DocxAdapter,
    MarkItDownFastTextViewProvider,
    PdfAdapter,
    PptxAdapter,
    XlsxAdapter,
)
from file_domain.models import FileResource
from file_runtime import FileAdapterRegistry, FileResourceResolver


class FileInput(BaseModel):
    file: str = Field(description="Logical filename shown in the Current Run Files manifest")


class FastTextInput(FileInput):
    max_chars: int = Field(default=32_000, ge=1, le=128_000)


class PdfPagesInput(FileInput):
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    max_chars: int = Field(default=32_000, ge=1, le=128_000)


class PdfTableInput(FileInput):
    page: int = Field(ge=1)
    max_rows: int = Field(default=100, ge=1, le=500)


class DocxParagraphsInput(FileInput):
    start: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=500)
    max_chars: int = Field(default=32_000, ge=1, le=128_000)


class DocxTableInput(FileInput):
    table_index: int = Field(default=0, ge=0)
    max_rows: int = Field(default=100, ge=1, le=500)


class SheetRangeInput(FileInput):
    sheet: str = Field(min_length=1, max_length=200)
    start_row: int = Field(ge=1)
    end_row: int = Field(ge=1)
    start_column: int = Field(ge=1)
    end_column: int = Field(ge=1)


class SlideInput(FileInput):
    slide: int = Field(ge=1)
    max_chars: int = Field(default=16_000, ge=1, le=64_000)


def default_file_adapter_registry() -> FileAdapterRegistry:
    registry = FileAdapterRegistry()
    registry.register(
        "fast_text", MarkItDownFastTextViewProvider(),
        extensions=("txt", "md", "html", "csv", "json", "xml", "pdf", "docx", "xlsx", "pptx"),
    )
    registry.register("pdf", PdfAdapter(), media_types=("application/pdf",), extensions=("pdf",))
    registry.register(
        "docx", DocxAdapter(),
        media_types=("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
        extensions=("docx",),
    )
    registry.register(
        "xlsx", XlsxAdapter(),
        media_types=("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",),
        extensions=("xlsx",),
    )
    registry.register(
        "pptx", PptxAdapter(),
        media_types=("application/vnd.openxmlformats-officedocument.presentationml.presentation",),
        extensions=("pptx",),
    )
    return registry


def _encode(
    resource: FileResource, payload: dict[str, Any], *, max_return_bytes: int = 512 * 1024
) -> str:
    payload = {
        "file": resource.logical_name,
        "file_id": str(resource.file_id),
        "scanned_bytes": resource.size_bytes,
        **payload,
    }
    payload["returned_bytes"] = 0
    while True:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        size = len(encoded.encode("utf-8"))
        if size > max_return_bytes:
            payload = {
                "file": resource.logical_name,
                "file_id": str(resource.file_id),
                "scanned_bytes": resource.size_bytes,
                "truncated": True,
                "error": "bounded result exceeded return limit; request a smaller range",
                "returned_bytes": 0,
            }
            continue
        if payload["returned_bytes"] == size:
            return encoded
        payload["returned_bytes"] = size


def create_file_read_tools(
    scope_provider: Callable[[], Any | None],
    registry: FileAdapterRegistry | None = None,
    document_router: Any | None = None,
    account_id_provider: Callable[[], str] | None = None,
) -> list[StructuredTool]:
    adapters = registry or default_file_adapter_registry()

    def resolve(file: str, capability: str) -> tuple[FileResource, Any]:
        scope = scope_provider()
        if scope is None:
            raise ValueError("Run file scope is unavailable")
        resource = FileResourceResolver(scope).resolve(file)
        return resource, adapters.resolve(capability, resource)

    async def _read_default_view(file: str, max_chars: int = 32_000) -> str:
        resource, adapter = resolve(file, "fast_text")
        if document_router is not None and document_router.should_normalize(resource):
            scope = scope_provider()
            account_id = account_id_provider() if account_id_provider is not None else ""
            if scope is None or not account_id:
                raise ValueError("document routing identity is unavailable")
            reference = await document_router.normalize(
                account_id, str(scope.run_id), resource
            )
            return _encode(resource, {
                "route": "normalized_document",
                "normalized_document_ref": reference,
                "truncated": bool(reference["truncated"]),
            })
        view = await asyncio.to_thread(adapter.convert, resource, max_chars=max_chars)
        return _encode(resource, {
            "route": "bounded_direct_read",
            "text": view.text, "media_type": view.media_type,
            "truncated": view.truncated, "metadata": view.metadata,
        })

    async def read_file(file: str, max_chars: int = 32_000) -> str:
        return await _read_default_view(file=file, max_chars=max_chars)

    async def inspect_pdf(file: str) -> str:
        resource, adapter = resolve(file, "pdf")
        return _encode(resource, await asyncio.to_thread(adapter.inspect, resource))

    async def read_pdf_pages(
        file: str, start_page: int, end_page: int, max_chars: int = 32_000
    ) -> str:
        resource, adapter = resolve(file, "pdf")
        result = await asyncio.to_thread(
            adapter.read_pages, resource, start_page, end_page, max_chars
        )
        return _encode(resource, result)

    async def extract_pdf_tables(file: str, page: int, max_rows: int = 100) -> str:
        resource, adapter = resolve(file, "pdf")
        result = await asyncio.to_thread(adapter.extract_tables, resource, page, max_rows)
        return _encode(resource, result)

    async def inspect_docx(file: str) -> str:
        resource, adapter = resolve(file, "docx")
        return _encode(resource, await asyncio.to_thread(adapter.inspect, resource))

    async def read_docx_paragraphs(
        file: str, start: int = 0, limit: int = 50, max_chars: int = 32_000
    ) -> str:
        resource, adapter = resolve(file, "docx")
        result = await asyncio.to_thread(
            adapter.read_paragraphs, resource, start, limit, max_chars
        )
        return _encode(resource, result)

    async def extract_docx_tables(
        file: str, table_index: int = 0, max_rows: int = 100
    ) -> str:
        resource, adapter = resolve(file, "docx")
        result = await asyncio.to_thread(
            adapter.extract_tables, resource, table_index, max_rows
        )
        return _encode(resource, result)

    async def inspect_workbook(file: str) -> str:
        resource, adapter = resolve(file, "xlsx")
        return _encode(resource, await asyncio.to_thread(adapter.inspect, resource))

    async def read_sheet_range(
        file: str, sheet: str, start_row: int, end_row: int,
        start_column: int, end_column: int,
    ) -> str:
        resource, adapter = resolve(file, "xlsx")
        result = await asyncio.to_thread(
            adapter.read_range, resource, sheet, start_row, end_row,
            start_column, end_column, 10_000,
        )
        return _encode(resource, result)

    async def inspect_presentation(file: str) -> str:
        resource, adapter = resolve(file, "pptx")
        return _encode(resource, await asyncio.to_thread(adapter.inspect, resource))

    async def read_slide(file: str, slide: int, max_chars: int = 16_000) -> str:
        resource, adapter = resolve(file, "pptx")
        result = await asyncio.to_thread(adapter.read_slide, resource, slide, max_chars)
        return _encode(resource, result)

    scope_hint = " Use the logical filename from Current Run Files; this tool only reads the Current Run File Scope."
    definitions = [
        ("read_file", "Read a bounded Markdown/text view of an uploaded or current-Run file." + scope_hint, FastTextInput, read_file),
        ("inspect_pdf", "Inspect PDF metadata and page count." + scope_hint, FileInput, inspect_pdf),
        ("read_pdf_pages", "Read a bounded one-based PDF page range." + scope_hint, PdfPagesInput, read_pdf_pages),
        ("extract_pdf_tables", "Extract bounded tables from one PDF page." + scope_hint, PdfTableInput, extract_pdf_tables),
        ("inspect_docx", "Inspect DOCX paragraph, heading, and table counts." + scope_hint, FileInput, inspect_docx),
        ("read_docx_paragraphs", "Read a bounded DOCX paragraph range." + scope_hint, DocxParagraphsInput, read_docx_paragraphs),
        ("extract_docx_tables", "Read bounded rows from one DOCX table." + scope_hint, DocxTableInput, extract_docx_tables),
        ("inspect_workbook", "List XLSX sheets before selecting a range." + scope_hint, FileInput, inspect_workbook),
        ("read_sheet_range", "Read a bounded XLSX row and column range." + scope_hint, SheetRangeInput, read_sheet_range),
        ("inspect_presentation", "Inspect PPTX slide count." + scope_hint, FileInput, inspect_presentation),
        ("read_slide", "Read bounded text from one PPTX slide." + scope_hint, SlideInput, read_slide),
    ]
    tools = [
        StructuredTool.from_function(
            name=name, description=description, args_schema=schema, coroutine=coroutine
        )
        for name, description, schema, coroutine in definitions
    ]
    for tool in tools:
        tool.metadata = {
            "side_effect_class": "read_only",
            "file_scope_required": True,
            "budget_reservation": {
                "tool_calls": 1,
                "bytes_scanned": 512 * 1024 * 1024,
                "bytes_returned_to_model": 512 * 1024,
            },
            "usage_json_fields": {
                "scanned_bytes": "bytes_scanned",
                "returned_bytes": "bytes_returned_to_model",
            },
            "trace_json_fields": {"truncated": "truncated"},
        }
    return tools
