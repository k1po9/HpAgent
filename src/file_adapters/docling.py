"""Docling v2 to HpAgent normalized document mapping."""

from __future__ import annotations

from typing import Any

from file_domain.models import (
    FileResource,
    NormalizedBlock,
    NormalizedDocument,
    NormalizedTable,
    SourceLocator,
)


class DoclingStructuredDocumentProvider:
    """Lazy-load Docling and keep its objects behind the provider boundary."""

    def __init__(self, converter: Any | None = None) -> None:
        self._converter = converter

    def _get_converter(self) -> Any:
        if self._converter is None:
            from docling.document_converter import DocumentConverter

            self._converter = DocumentConverter()
        return self._converter

    @staticmethod
    def _locator(resource: FileResource, item: Any) -> SourceLocator:
        provenance = list(getattr(item, "prov", ()) or ())
        first = provenance[0] if provenance else None
        page = getattr(first, "page_no", None)
        raw_bbox = getattr(first, "bbox", None)
        bbox = None
        if raw_bbox is not None:
            bbox = tuple(
                float(getattr(raw_bbox, name)) for name in ("l", "t", "r", "b")
            )
        return SourceLocator(
            resource.file_id,
            resource.logical_name,
            page=int(page) if page else None,
            bbox=bbox,
        )

    def parse(
        self,
        resource: FileResource,
        *,
        max_blocks: int = 2_000,
        max_tables: int = 100,
        max_table_rows: int = 1_000,
        max_chars: int = 2_000_000,
    ) -> NormalizedDocument:
        if min(max_blocks, max_tables, max_table_rows, max_chars) < 1:
            raise ValueError("Docling normalization limits must be positive")
        result = self._get_converter().convert(resource.local_path)
        document = result.document
        blocks: list[NormalizedBlock] = []
        tables: list[NormalizedTable] = []
        used_chars = 0
        truncated = False
        for item, level in document.iterate_items():
            if hasattr(item, "export_to_dataframe"):
                if len(tables) >= max_tables:
                    truncated = True
                    continue
                frame = item.export_to_dataframe(doc=document)
                raw_rows = frame.values.tolist()
                if len(raw_rows) > max_table_rows:
                    raw_rows = raw_rows[:max_table_rows]
                    truncated = True
                tables.append(NormalizedTable(
                    tuple(str(value) for value in frame.columns),
                    tuple(tuple(row) for row in raw_rows),
                    self._locator(resource, item),
                    {"adapter": "docling", "hierarchy_level": int(level)},
                ))
                continue
            text = str(getattr(item, "text", "") or "")
            if not text:
                continue
            if len(blocks) >= max_blocks or used_chars >= max_chars:
                truncated = True
                continue
            remaining = max_chars - used_chars
            rendered = text[:remaining]
            label = getattr(item, "label", type(item).__name__)
            blocks.append(NormalizedBlock(
                kind=str(getattr(label, "value", label)),
                text=rendered,
                locator=self._locator(resource, item),
                metadata={"hierarchy_level": int(level)},
            ))
            used_chars += len(rendered)
            if len(rendered) < len(text):
                truncated = True
        return NormalizedDocument(
            resource,
            tuple(blocks),
            tuple(tables),
            {"adapter": "docling", "truncated": truncated},
        )
