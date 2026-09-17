from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from file_adapters.docling import DoclingStructuredDocumentProvider
from file_adapters.markitdown import MarkItDownFastTextViewProvider
from file_domain.models import (
    FileResource,
    NormalizedTable,
    SourceLocator,
)
from file_runtime.registry import FileAdapterRegistry
from file_runtime.resolver import FileResourceResolver
from workspace.file_scope import RunFileInput, RunFileScope


def _scope(tmp_path: Path, *, content_type: str | None = "text/plain") -> RunFileScope:
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    (inputs / "notes.txt").write_text("hello", encoding="utf-8")
    item = RunFileInput(
        uuid4(), "notes.txt", 5, "utf-8", content_type, "a" * 64
    )
    return RunFileScope(uuid4(), inputs, tmp_path / "scratch", tmp_path / "outputs", (item,))


def test_resolver_uses_only_authoritative_run_inputs(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    resolver = FileResourceResolver(scope)

    by_name = resolver.resolve("notes.txt")
    by_id = resolver.resolve(scope.inputs[0].file_id)

    assert by_name == by_id
    assert by_name.media_type == "text/plain"
    assert by_name.sha256 == "a" * 64
    with pytest.raises(LookupError, match="not an input"):
        resolver.resolve("../secret.txt")


def test_resolver_rejects_symlink_even_when_manifest_names_it(tmp_path: Path) -> None:
    scope = _scope(tmp_path)
    (scope.inputs_root / "notes.txt").unlink()
    (scope.inputs_root / "notes.txt").symlink_to(tmp_path / "secret.txt")
    with pytest.raises(ValueError, match="escapes"):
        FileResourceResolver(scope).resolve("notes.txt")


def test_registry_is_capability_aware_and_deterministic(tmp_path: Path) -> None:
    resource = FileResource(
        uuid4(), "notes.txt", tmp_path / "notes.txt", 0, "text/plain"
    )
    lower, higher = object(), object()
    registry = FileAdapterRegistry()
    registry.register("fast_text", lower, extensions=("txt",), priority=1)
    registry.register("fast_text", higher, media_types=("text/plain",), priority=2)

    assert registry.resolve("fast_text", resource) is higher
    with pytest.raises(LookupError, match="no structured"):
        registry.resolve("structured", resource)


def test_registry_rejects_ambiguous_highest_priority(tmp_path: Path) -> None:
    resource = FileResource(
        uuid4(), "notes.txt", tmp_path / "notes.txt", 0, "text/plain"
    )
    registry = FileAdapterRegistry()
    registry.register("fast_text", object(), extensions=(".txt",), priority=1)
    registry.register("fast_text", object(), media_types=("text/plain",), priority=1)
    with pytest.raises(LookupError, match="ambiguous"):
        registry.resolve("fast_text", resource)


def test_markitdown_view_is_bounded_and_does_not_leak_host_path(tmp_path: Path) -> None:
    resource = FileResource(
        uuid4(), "notes.docx", tmp_path / "private" / "notes.docx", 10,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    converter = SimpleNamespace(
        convert=lambda path: SimpleNamespace(text_content="abcdefghij")
    )
    view = MarkItDownFastTextViewProvider(converter).convert(resource, max_chars=5)

    assert view.text == "abcde"
    assert view.truncated is True
    assert view.locator.logical_name == "notes.docx"
    assert str(tmp_path) not in repr(view.metadata)


def test_normalized_models_validate_source_locations_and_table_shape() -> None:
    locator = SourceLocator(uuid4(), "report.xlsx", sheet="Data", cell_range="A1:B2")
    table = NormalizedTable(("a", "b"), ((1, 2),), locator)
    assert table.rows == ((1, 2),)
    with pytest.raises(ValueError, match="slide must be one-based"):
        SourceLocator(uuid4(), "slides.pptx", slide=0)
    with pytest.raises(ValueError, match="column width"):
        NormalizedTable(("a", "b"), ((1,),), locator)
    with pytest.raises(ValueError, match="requires sheet"):
        SourceLocator(uuid4(), "report.xlsx", cell_range="A1")


def test_docling_mapper_returns_only_hpagent_normalized_models(tmp_path: Path) -> None:
    resource = FileResource(
        uuid4(), "report.pdf", tmp_path / "report.pdf", 10, "application/pdf"
    )
    text_item = SimpleNamespace(
        text="A structured paragraph",
        label=SimpleNamespace(value="paragraph"),
        prov=[SimpleNamespace(page_no=2)],
    )
    frame = SimpleNamespace(
        columns=["name", "value"],
        values=SimpleNamespace(tolist=lambda: [["alpha", 1]]),
    )
    table_item = SimpleNamespace(
        prov=[SimpleNamespace(page_no=3)],
        export_to_dataframe=lambda **kwargs: frame,
    )
    document = SimpleNamespace(
        iterate_items=lambda: iter(((text_item, 1), (table_item, 2)))
    )
    converter = SimpleNamespace(
        convert=lambda path: SimpleNamespace(document=document)
    )

    normalized = DoclingStructuredDocumentProvider(converter).parse(resource)

    assert normalized.blocks[0].text == "A structured paragraph"
    assert normalized.blocks[0].locator.page == 2
    assert normalized.tables[0].rows == (("alpha", 1),)
    assert normalized.tables[0].locator.page == 3
    assert normalized.metadata == {"adapter": "docling", "truncated": False}
