"""Ownership-scoped upload lifecycle for FILE-P0-03."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from collections.abc import AsyncIterable
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from persistence.repositories import ConversationRepository, FileRepository, IdempotencyRepository
from persistence.uow import UnitOfWork, retryable_transaction
from storage.tenant_file_store import (
    FileEncodingUnsupported as StoreEncodingUnsupported,
)
from storage.tenant_file_store import (
    FileHashMismatch as StoreHashMismatch,
)
from storage.tenant_file_store import (
    FileStoreError,
    TenantFileStore,
)
from storage.tenant_file_store import (
    FileTooLarge as StoreTooLarge,
)

from .errors import (
    FileAlreadyBound,
    FileEncodingUnsupported,
    FileHashMismatch,
    FileTooLarge,
    FileUploadInvalid,
    IdempotencyConflict,
    ResourceNotFound,
    UnsupportedFileType,
)
from .services import CommandResult

ALLOWED_DECLARED_TYPES = {
    "text/plain", "text/x-log", "application/log", "application/octet-stream",
}


class FileService:
    def __init__(
        self,
        database: object,
        store: TenantFileStore,
        *,
        max_bytes: int,
        upload_ttl: timedelta = timedelta(hours=1),
        unbound_ready_ttl: timedelta = timedelta(hours=24),
    ) -> None:
        if upload_ttl.total_seconds() <= 0 or unbound_ready_ttl.total_seconds() <= 0:
            raise ValueError("file TTLs must be positive")
        self.database = database
        self.store = store
        self.max_bytes = max_bytes
        self.conversations = ConversationRepository()
        self.files = FileRepository()
        self.idempotency = IdempotencyRepository()
        self.upload_ttl = upload_ttl
        self.unbound_ready_ttl = unbound_ready_ttl

    @retryable_transaction
    def create_upload(
        self, account_id: UUID, conversation_id: UUID, key: str,
        file_name: str, size_bytes: int, content_type: str,
        sha256: str | None,
    ) -> CommandResult:
        if size_bytes < 0 or size_bytes > self.max_bytes:
            raise FileTooLarge()
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        if normalized_type not in ALLOWED_DECLARED_TYPES:
            raise UnsupportedFileType()
        original_name, display_name = self._names(file_name)
        payload = {
            "conversation_id": str(conversation_id), "file_name": original_name,
            "size_bytes": size_bytes, "content_type": normalized_type, "sha256": sha256,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).digest()
        with UnitOfWork(self.database) as uow:
            existing = self.idempotency.claim(
                uow, uuid7(), account_id, "create_upload", key, digest,
                datetime.now(UTC) + timedelta(hours=24),
            )
            if existing:
                if bytes(existing["request_hash"]) != digest:
                    raise IdempotencyConflict()
                if existing["status"] == "completed":
                    return CommandResult(
                        int(existing["response_status"]), existing["response_body"], replayed=True
                    )
                raise FileUploadInvalid()
            if not self.conversations.lock_active(uow, account_id, conversation_id):
                raise ResourceNotFound()
            file_id = uuid7()
            self.files.insert_upload(
                uow, file_id, account_id, conversation_id, original_name, display_name,
                size_bytes, normalized_type, sha256,
                datetime.now(UTC) + self.upload_ttl,
            )
            body = {
                "file": self._dto(self.files.get_for_account(uow, account_id, file_id)),
                "content_url": f"/api/v1/uploads/{file_id}/content",
            }
            self.idempotency.complete(
                uow, account_id, "create_upload", key, 201, json.dumps(body)
            )
            return CommandResult(201, body)

    async def upload_content(
        self, account_id: UUID, file_id: UUID, chunks: AsyncIterable[bytes],
    ) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = self.files.get_for_account(uow, account_id, file_id)
            if row is None:
                raise ResourceNotFound()
            if row["status"] != "uploading":
                raise FileUploadInvalid()
            declared_size = int(row["size_bytes"])
            declared_sha256 = row.get("declared_sha256")
        try:
            staged = await self.store.stage_async(
                file_id, chunks, declared_size=declared_size,
                declared_sha256=declared_sha256,
            )
            published = self.store.publish(account_id, file_id, staged)
        except StoreTooLarge as exc:
            self._reject(account_id, file_id, "file_too_large")
            raise FileTooLarge() from exc
        except StoreHashMismatch as exc:
            self._reject(account_id, file_id, "file_hash_mismatch")
            raise FileHashMismatch() from exc
        except StoreEncodingUnsupported as exc:
            self._reject(account_id, file_id, "file_encoding_unsupported")
            raise FileEncodingUnsupported() from exc
        except FileExistsError as exc:
            # A concurrent/retried PUT may already own the stable staging key.
            # Do not reject the database row while that upload can still finish.
            raise FileUploadInvalid() from exc
        except FileStoreError as exc:
            self._reject(account_id, file_id, "file_upload_invalid")
            raise FileUploadInvalid() from exc
        with UnitOfWork(self.database) as uow:
            marked_ready = self.files.mark_ready(
                uow, account_id, file_id, published.storage_key,
                published.size_bytes, published.sha256, staged.encoding,
                datetime.now(UTC) + self.unbound_ready_ttl,
            )
        if not marked_ready:
            self.store.delete(published.storage_key)
            raise FileUploadInvalid()
        with UnitOfWork(self.database) as uow:
            row = self.files.get_for_account(uow, account_id, file_id)
            return self._dto(row)

    def get(self, account_id: UUID, file_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = self.files.get_for_account(uow, account_id, file_id)
            if row is None or row["status"] == "deleted":
                raise ResourceNotFound()
            return self._dto(row)

    def download(self, account_id: UUID, file_id: UUID) -> tuple[dict[str, Any], Any]:
        with UnitOfWork(self.database) as uow:
            row = self.files.get_for_account(uow, account_id, file_id)
            if row is None or row["status"] != "ready" or not row["storage_key"]:
                raise ResourceNotFound()
            metadata = dict(row)
        return self._dto(metadata), self.store.open(str(metadata["storage_key"]))

    def delete(self, account_id: UUID, file_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            result = self.files.mark_deleted_if_unbound(uow, account_id, file_id)
            if result == "not_found":
                raise ResourceNotFound()
            if result == "bound":
                raise FileAlreadyBound()

    def _reject(self, account_id: UUID, file_id: UUID, code: str) -> None:
        with UnitOfWork(self.database) as uow:
            self.files.mark_rejected(uow, account_id, file_id, code)

    @staticmethod
    def _names(file_name: str) -> tuple[str, str]:
        original = unicodedata.normalize("NFC", file_name).strip()
        if not original or len(original) > 255 or any(ord(char) < 32 for char in original):
            raise FileUploadInvalid()
        display = original.replace("\\", "/").rsplit("/", 1)[-1].strip()
        if not display or display in {".", ".."}:
            raise FileUploadInvalid()
        return original, display

    @staticmethod
    def _dto(row: Any) -> dict[str, Any]:
        return {
            "file_id": str(row["file_id"]), "file_name": row["display_name"],
            "purpose": row["purpose"], "status": row["status"],
            "size_bytes": row["size_bytes"], "content_type": row["content_type"],
            "encoding": row["encoding"], "sha256": row["sha256"],
            "failure_code": row["failure_code"],
            "download_url": (
                f"/api/v1/files/{row['file_id']}/content" if row["status"] == "ready" else None
            ),
        }
