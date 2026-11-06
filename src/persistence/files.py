"""FileRepository SQL operations in the caller-owned transaction."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from .uow import UnitOfWork


class FileRepository:
    def list_ready_for_run(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        run_id: UUID,
    ) -> list[dict[str, Any]]:
        """Return the model-safe manifest for one account-owned Run."""
        return list(
            uow.execute(
                "SELECT rf.logical_name,rf.direction,sf.size_bytes,sf.encoding,sf.content_type "
                "FROM run_files rf JOIN stored_files sf ON sf.account_id=rf.account_id "
                "AND sf.file_id=rf.file_id "
                "WHERE rf.account_id=%s AND rf.run_id=%s AND sf.status='ready' "
                "ORDER BY rf.direction,rf.logical_name",
                (account_id, run_id),
            ).fetchall()
        )

    def insert_upload(
        self,
        uow: UnitOfWork,
        file_id: UUID,
        account_id: UUID,
        conversation_id: UUID | None,
        original_name: str,
        display_name: str,
        declared_size: int,
        content_type: str,
        declared_sha256: str | None,
        expires_at: datetime,
        *,
        source_workspace_id: UUID | None = None,
    ) -> None:
        uow.execute(
            "INSERT INTO stored_files(file_id,account_id,conversation_id,source_workspace_id,purpose,status,"
            "original_name,display_name,content_type,size_bytes,declared_sha256,expires_at) "
            "VALUES (%s,%s,%s,%s,'input','uploading',%s,%s,%s,%s,%s,%s)",
            (
                file_id,
                account_id,
                conversation_id,
                source_workspace_id,
                original_name,
                display_name,
                content_type,
                declared_size,
                declared_sha256,
                expires_at,
            ),
        )

    def get_for_account(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
        *,
        lock: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM stored_files WHERE account_id=%s AND file_id=%s" + suffix,
                (account_id, file_id),
            ).fetchone(),
        )

    def mark_ready(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
        storage_key: str,
        size_bytes: int,
        sha256: str,
        encoding: str,
        expires_at: datetime,
    ) -> bool:
        return (
            uow.execute(
                "UPDATE stored_files SET status='ready',storage_key=%s,size_bytes=%s,sha256=%s,"
                "encoding=%s,ready_at=now(),expires_at=%s "
                "WHERE account_id=%s AND file_id=%s AND status='uploading' RETURNING file_id",
                (storage_key, size_bytes, sha256, encoding, expires_at, account_id, file_id),
            ).fetchone()
            is not None
        )

    def mark_rejected(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
        failure_code: str,
    ) -> None:
        uow.execute(
            "UPDATE stored_files SET status='rejected',failure_code=%s,storage_key=NULL,"
            "ready_at=NULL,sha256=NULL WHERE account_id=%s AND file_id=%s "
            "AND status='uploading'",
            (failure_code, account_id, file_id),
        )

    def mark_deleted_if_unbound(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
    ) -> str:
        from web_domain.file_lifecycle import claim_file_deletion

        return cast(str, claim_file_deletion(uow, account_id, file_id))

    def lock_ready_inputs(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID,
        file_ids: Collection[UUID],
    ) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        return list(
            uow.execute(
                "SELECT * FROM stored_files WHERE account_id=%s "
                "AND file_id=ANY(%s) AND status='ready' "
                "ORDER BY created_at,file_id FOR UPDATE",
                (account_id, list(file_ids)),
            ).fetchall()
        )

    def bind_inputs(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
        files: list[dict[str, Any]],
    ) -> None:
        used_names: set[str] = set()
        if files:
            uow.execute(
                "UPDATE stored_files SET expires_at=NULL WHERE account_id=%s "
                "AND purpose='input' AND file_id=ANY(%s)",
                (account_id, [file["file_id"] for file in files]),
            )
        for ordinal, file in enumerate(files):
            logical_name = self._unique_logical_name(str(file["display_name"]), used_names)
            uow.execute(
                "INSERT INTO message_files(account_id,conversation_id,message_id,file_id,role,ordinal) "
                "VALUES (%s,%s,%s,%s,'input',%s)",
                (account_id, conversation_id, message_id, file["file_id"], ordinal),
            )
            uow.execute(
                "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,logical_name) "
                "VALUES (%s,%s,%s,%s,'input',%s)",
                (account_id, conversation_id, run_id, file["file_id"], logical_name),
            )

    def copy_retry_inputs(
        self,
        uow: UnitOfWork,
        source_run_id: UUID,
        target_run_id: UUID,
    ) -> None:
        uow.execute(
            "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,logical_name) "
            "SELECT account_id,conversation_id,%s,file_id,'input',logical_name FROM run_files "
            "WHERE run_id=%s AND direction='input'",
            (target_run_id, source_run_id),
        )

    @staticmethod
    def _unique_logical_name(name: str, used: set[str]) -> str:
        safe = name.replace("/", "_").replace("\\", "_").strip() or "file"
        safe = safe[:255]
        candidate = safe
        stem, dot, suffix = safe.rpartition(".")
        base = stem if dot else safe
        extension = f".{suffix}" if dot else ""
        counter = 2
        while candidate.casefold() in used:
            marker = f"-{counter}"
            candidate = f"{base[: 255 - len(extension) - len(marker)]}{marker}{extension}"
            counter += 1
        used.add(candidate.casefold())
        return candidate
