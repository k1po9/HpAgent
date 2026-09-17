"""Stable domain contracts for File Assistant."""

from .models import (
    DocumentPatch,
    FileResource,
    NormalizedBlock,
    NormalizedDocument,
    NormalizedTable,
    SourceLocator,
    TextView,
)
from .serialization import locator_to_dict, normalized_document_to_dict

__all__ = [
    "DocumentPatch",
    "FileResource",
    "NormalizedBlock",
    "NormalizedDocument",
    "NormalizedTable",
    "SourceLocator",
    "TextView",
    "locator_to_dict",
    "normalized_document_to_dict",
]
