"""Compact Temporal payloads for document normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypedDict


@dataclass(frozen=True)
class NormalizeDocumentInput:
    schema_version: int
    account_id: str
    run_id: str
    file_id: str
    operation_id: str


class NormalizedDocumentRef(TypedDict):
    schema_version: int
    run_id: str
    file_id: str
    document_ref: str
    block_count: int
    table_count: int
    truncated: bool
