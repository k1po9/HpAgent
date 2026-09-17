"""Ports implemented by File Assistant adapters."""

from __future__ import annotations

from typing import Protocol

from .models import FileResource, NormalizedDocument, TextView


class FastTextViewProvider(Protocol):
    def convert(self, resource: FileResource, *, max_chars: int) -> TextView: ...


class StructuredDocumentProvider(Protocol):
    def parse(self, resource: FileResource) -> NormalizedDocument: ...


class ConversionProvider(Protocol):
    async def convert(self, resource: FileResource, target_media_type: str) -> bytes: ...
