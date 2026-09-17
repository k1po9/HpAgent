"""Stable JSON representation for persisted normalized documents."""

from __future__ import annotations

from typing import Any

from .models import NormalizedDocument, SourceLocator


def locator_to_dict(locator: SourceLocator) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "file_id": str(locator.file_id),
            "logical_name": locator.logical_name,
            "page": locator.page,
            "slide": locator.slide,
            "paragraph_index": locator.paragraph_index,
            "table_index": locator.table_index,
            "sheet": locator.sheet,
            "cell_range": locator.cell_range,
            "section": locator.section,
            "bbox": list(locator.bbox) if locator.bbox else None,
        }.items()
        if value is not None
    }


def normalized_document_to_dict(document: NormalizedDocument) -> dict[str, Any]:
    """Exclude the worker-local path from the durable representation."""
    resource = document.resource
    return {
        "schema_version": 1,
        "resource": {
            "file_id": str(resource.file_id),
            "logical_name": resource.logical_name,
            "size_bytes": resource.size_bytes,
            "media_type": resource.media_type,
            "encoding": resource.encoding,
            "sha256": resource.sha256,
        },
        "blocks": [
            {
                "kind": block.kind,
                "text": block.text,
                "source_locator": locator_to_dict(block.locator),
                "metadata": block.metadata,
            }
            for block in document.blocks
        ],
        "tables": [
            {
                "columns": list(table.columns),
                "rows": [list(row) for row in table.rows],
                "source_locator": locator_to_dict(table.locator),
                "metadata": table.metadata,
            }
            for table in document.tables
        ],
        "metadata": document.metadata,
    }
