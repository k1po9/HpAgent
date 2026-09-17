"""Local MarkItDown fast text view adapter."""

from __future__ import annotations

from typing import Any

from file_domain.models import FileResource, SourceLocator, TextView


class MarkItDownFastTextViewProvider:
    def __init__(self, converter: Any | None = None) -> None:
        self._converter = converter

    def _get_converter(self) -> Any:
        if self._converter is None:
            from markitdown import MarkItDown

            self._converter = MarkItDown(enable_plugins=False)
        return self._converter

    def convert(self, resource: FileResource, *, max_chars: int) -> TextView:
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        result = self._get_converter().convert(resource.local_path)
        text = str(result.text_content or "")
        truncated = len(text) > max_chars
        return TextView(
            text=text[:max_chars],
            locator=SourceLocator(resource.file_id, resource.logical_name),
            truncated=truncated,
            metadata={"adapter": "markitdown", "source_media_type": resource.media_type},
        )
