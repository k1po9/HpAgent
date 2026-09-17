"""Library-independent File Assistant value objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class FileResource:
    """An input already authorized by the active RunFileScope."""

    file_id: UUID
    logical_name: str
    local_path: Path
    size_bytes: int
    media_type: str
    encoding: str | None = None
    sha256: str | None = None


@dataclass(frozen=True)
class SourceLocator:
    """Stable logical location; never exposes a worker filesystem path."""

    file_id: UUID
    logical_name: str
    page: int | None = None
    slide: int | None = None
    paragraph_index: int | None = None
    table_index: int | None = None
    sheet: str | None = None
    cell_range: str | None = None
    section: str | None = None
    bbox: tuple[float, float, float, float] | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            raise ValueError("page must be one-based")
        if self.slide is not None and self.slide < 1:
            raise ValueError("slide must be one-based")
        if self.paragraph_index is not None and self.paragraph_index < 0:
            raise ValueError("paragraph_index must be non-negative")
        if self.table_index is not None and self.table_index < 0:
            raise ValueError("table_index must be non-negative")
        if self.cell_range and not self.sheet:
            raise ValueError("cell_range requires sheet")


@dataclass(frozen=True)
class NormalizedBlock:
    kind: str
    text: str
    locator: SourceLocator
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedTable:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    locator: SourceLocator
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        width = len(self.columns)
        if any(len(row) != width for row in self.rows):
            raise ValueError("table rows must match column width")


@dataclass(frozen=True)
class NormalizedDocument:
    resource: FileResource
    blocks: tuple[NormalizedBlock, ...] = ()
    tables: tuple[NormalizedTable, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextView:
    text: str
    locator: SourceLocator
    media_type: str = "text/markdown"
    truncated: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentPatch:
    """Bounded, declarative edit that always produces a new file version."""

    source_file_id: UUID
    operation: str
    parameters: dict[str, Any]
    output_name: str

    def __post_init__(self) -> None:
        if self.operation not in {
            "replace_docx_text", "append_docx_section",
            "write_sheet_range", "replace_slide",
        }:
            raise ValueError("unsupported document patch operation")
        candidate = Path(self.output_name)
        if (
            not self.output_name or candidate.is_absolute()
            or len(candidate.parts) != 1 or self.output_name in {".", ".."}
        ):
            raise ValueError("invalid patch output name")
