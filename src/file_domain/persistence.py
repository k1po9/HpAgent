"""PostgreSQL repository for normalized File Assistant results."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork


class NormalizedDocumentRepository:
    def find_by_operation(
        self, uow: UnitOfWork, operation_id: str
    ) -> dict[str, Any] | None:
        row = uow.execute(
            "SELECT document_ref,run_id,file_id,block_count,table_count,truncated,"
            "COALESCE((normalized_document->'resource'->>'size_bytes')::bigint,0) "
            "AS scanned_bytes "
            "FROM normalized_documents WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        return dict(row) if row is not None else None

    def insert(
        self, uow: UnitOfWork, *, document_ref: str, operation_id: str,
        account_id: UUID, run_id: UUID, file_id: UUID, document: dict[str, Any],
        block_count: int, table_count: int, truncated: bool,
    ) -> dict[str, Any]:
        row = uow.execute(
            "INSERT INTO normalized_documents(document_ref,operation_id,account_id,run_id,"
            "file_id,normalized_document,block_count,table_count,truncated) "
            "VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) "
            "ON CONFLICT (operation_id) DO NOTHING RETURNING document_ref,run_id,file_id,"
            "block_count,table_count,truncated",
            (
                document_ref, operation_id, account_id, run_id, file_id,
                json.dumps(document, ensure_ascii=False, default=str),
                block_count, table_count, truncated,
            ),
        ).fetchone()
        if row is not None:
            return dict(row)
        existing = self.find_by_operation(uow, operation_id)
        if existing is None:
            raise RuntimeError("normalized document idempotency conflict")
        if existing["run_id"] != run_id or existing["file_id"] != file_id:
            raise RuntimeError("normalized document operation belongs to another input")
        return existing
