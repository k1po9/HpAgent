"""Recoverable TTL cleanup for unbound file objects and upload staging."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork, retryable_transaction
from storage.tenant_file_store import FileStoreError, TenantFileStore


@dataclass(frozen=True)
class FileCleanupResult:
    claimed: int
    deleted: int
    failed: int


class FileCleanupService:
    def __init__(self, database: object, store: TenantFileStore):
        self.database = database
        self.store = store

    def cleanup_once(self, *, limit: int = 100) -> FileCleanupResult:
        if limit < 1 or limit > 1000:
            raise ValueError("cleanup limit must be between 1 and 1000")
        rows = self._claim(limit)
        deleted = 0
        for row in rows:
            file_id = UUID(str(row["file_id"]))
            try:
                storage_key = row.get("storage_key")
                if storage_key:
                    self.store.delete(str(storage_key))
                else:
                    self.store.delete_staging(file_id)
                    self.store.delete(self.store.storage_key(
                        UUID(str(row["account_id"])), file_id
                    ))
            except (OSError, FileStoreError):
                continue
            self._complete(file_id)
            deleted += 1
        return FileCleanupResult(len(rows), deleted, len(rows) - deleted)

    @retryable_transaction
    def _claim(self, limit: int) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            rows = list(uow.execute(
                "SELECT sf.file_id,sf.account_id,sf.storage_key FROM stored_files sf "
                "WHERE sf.expires_at<=now() AND sf.status IN "
                "('uploading','ready','rejected','deleted') "
                "AND NOT EXISTS (SELECT 1 FROM message_files mf "
                "WHERE mf.file_id=sf.file_id) "
                "AND NOT EXISTS (SELECT 1 FROM run_files rf "
                "WHERE rf.file_id=sf.file_id) "
                "ORDER BY sf.expires_at,sf.file_id FOR UPDATE SKIP LOCKED LIMIT %s",
                (limit,),
            ).fetchall())
            for row in rows:
                uow.execute(
                    "UPDATE stored_files SET status='deleted',deleted_at=COALESCE(deleted_at,now()) "
                    "WHERE file_id=%s",
                    (row["file_id"],),
                )
            return rows

    @retryable_transaction
    def _complete(self, file_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE stored_files SET storage_key=NULL,expires_at=NULL "
                "WHERE file_id=%s AND status='deleted'",
                (file_id,),
            )
