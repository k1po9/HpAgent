"""Bounded PDF reads using pypdf and pdfplumber."""

from __future__ import annotations

from typing import Any

from file_domain.models import FileResource


class PdfAdapter:
    def inspect(self, resource: FileResource) -> dict[str, Any]:
        from pypdf import PdfReader

        reader = PdfReader(resource.local_path)
        metadata = reader.metadata or {}
        return {
            "pages": len(reader.pages),
            "title": metadata.get("/Title"),
            "author": metadata.get("/Author"),
            "encrypted": reader.is_encrypted,
        }

    def read_pages(
        self, resource: FileResource, start_page: int, end_page: int, max_chars: int
    ) -> dict[str, Any]:
        from pypdf import PdfReader

        reader = PdfReader(resource.local_path)
        if start_page < 1 or end_page < start_page or end_page > len(reader.pages):
            raise ValueError("invalid PDF page range")
        pages: list[dict[str, Any]] = []
        used = 0
        truncated = False
        for number in range(start_page, end_page + 1):
            text = reader.pages[number - 1].extract_text() or ""
            remaining = max_chars - used
            if remaining <= 0:
                truncated = True
                break
            rendered = text[:remaining]
            pages.append({"page": number, "text": rendered})
            used += len(rendered)
            if len(rendered) < len(text):
                truncated = True
                break
        return {"pages": pages, "truncated": truncated}

    def extract_tables(
        self, resource: FileResource, page: int, max_rows: int
    ) -> dict[str, Any]:
        import pdfplumber

        with pdfplumber.open(resource.local_path) as document:
            if page < 1 or page > len(document.pages):
                raise ValueError("invalid PDF page")
            tables = document.pages[page - 1].extract_tables()
        normalized = []
        truncated = False
        for table in tables:
            rows = [[cell or "" for cell in row] for row in table]
            if len(rows) > max_rows:
                rows = rows[:max_rows]
                truncated = True
            normalized.append({"page": page, "rows": rows})
        return {"tables": normalized, "truncated": truncated}
