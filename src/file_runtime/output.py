"""Idempotent publication of immutable Run output files."""

from __future__ import annotations

import mimetypes
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid5

from persistence.uow import UnitOfWork
from storage.tenant_file_store import FileStoreError, TenantFileStore
from workspace.file_scope import RunFileScope

_OUTPUT_NAMESPACE = UUID("cf564253-0dc4-4a67-81a4-fc05e34212c1")


@dataclass(frozen=True)
class PublishedOutput:
    file_id: UUID
    logical_name: str
    content_type: str
    size_bytes: int
    sha256: str
    parent_file_id: UUID | None = None
    version: int = 1
    deduplicated: bool = False


class OutputPublisher:
    """Publish a validated scope output and register it in PostgreSQL once."""

    def __init__(self, database: object, store: TenantFileStore) -> None:
        self.database = database
        self.store = store

    def publish(
        self,
        scope: RunFileScope,
        operation_id: str,
        logical_name: str,
        content_type: str | None = None,
        parent_file_id: UUID | None = None,
        encoding: str = "binary",
    ) -> PublishedOutput:
        if not operation_id or len(operation_id) > 200:
            raise ValueError("operation_id must contain 1 to 200 characters")
        name = self._logical_name(logical_name)
        existing = self._existing(scope.run_id, operation_id)
        if existing is not None:
            if existing.logical_name != name or existing.parent_file_id != parent_file_id:
                raise RuntimeError("output operation belongs to another logical file")
            return PublishedOutput(**{**existing.__dict__, "deduplicated": True})

        source = (scope.outputs_root / name).absolute()
        if not source.is_relative_to(scope.outputs_root) or source.is_symlink():
            raise FileStoreError("output escapes the active Run scope")
        file_id = uuid5(_OUTPUT_NAMESPACE, f"{scope.run_id}:{operation_id}")
        staged = self.store.stage_output(file_id, source)
        with UnitOfWork(self.database) as uow:
            subject = uow.execute(
                "SELECT account_id,conversation_id FROM runs WHERE run_id=%s FOR UPDATE",
                (scope.run_id,),
            ).fetchone()
            if subject is None:
                self.store.delete_staging(file_id)
                raise RuntimeError("authoritative Run is unavailable")
            conflict = self._existing_in_uow(uow, scope.run_id, operation_id)
            if conflict is not None:
                self.store.delete_staging(file_id)
                if conflict.logical_name != name or conflict.parent_file_id != parent_file_id:
                    raise RuntimeError("output operation belongs to another logical file")
                return PublishedOutput(**{**conflict.__dict__, "deduplicated": True})
            published = self.store.publish(subject["account_id"], file_id, staged)
            media_type = content_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
            uow.execute(
                "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
                "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
                "parent_file_id,ready_at) VALUES (%s,%s,%s,'output','ready',%s,%s,%s,%s,"
                "%s,%s,%s,%s,now()) RETURNING version",
                (file_id, subject["account_id"], subject["conversation_id"], name, name,
                 published.storage_key, media_type, encoding, published.size_bytes,
                 published.sha256, parent_file_id),
            ).fetchone()
            uow.execute(
                "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,"
                "logical_name,operation_id) VALUES (%s,%s,%s,%s,'output',%s,%s)",
                (subject["account_id"], subject["conversation_id"], scope.run_id,
                 file_id, name, operation_id),
            )
            stored = uow.execute(
                "SELECT version FROM stored_files WHERE file_id=%s", (file_id,)
            ).fetchone()
            return PublishedOutput(
                file_id, name, media_type, published.size_bytes, published.sha256,
                parent_file_id, int(stored["version"]),
            )

    def replay(
        self,
        scope: RunFileScope,
        operation_id: str,
        logical_name: str,
        parent_file_id: UUID | None = None,
    ) -> PublishedOutput | None:
        """Return an already-published output before an adapter rewrites its path."""
        if not operation_id:
            return None
        name = self._logical_name(logical_name)
        existing = self._existing(scope.run_id, operation_id)
        if existing is None:
            return None
        if existing.logical_name != name or existing.parent_file_id != parent_file_id:
            raise RuntimeError("output operation belongs to another logical file")
        return PublishedOutput(**{**existing.__dict__, "deduplicated": True})

    def _existing(self, run_id: UUID, operation_id: str) -> PublishedOutput | None:
        with UnitOfWork(self.database) as uow:
            return self._existing_in_uow(uow, run_id, operation_id)

    @staticmethod
    def _existing_in_uow(
        uow: UnitOfWork, run_id: UUID, operation_id: str
    ) -> PublishedOutput | None:
        row = uow.execute(
            "SELECT rf.file_id,rf.logical_name,sf.content_type,sf.size_bytes,sf.sha256,"
            "sf.parent_file_id,sf.version "
            "FROM run_files rf JOIN stored_files sf ON sf.file_id=rf.file_id "
            "WHERE rf.run_id=%s AND rf.operation_id=%s AND rf.direction='output' "
            "AND sf.status='ready'",
            (run_id, operation_id),
        ).fetchone()
        if row is None:
            return None
        return PublishedOutput(
            row["file_id"], str(row["logical_name"]), str(row["content_type"]),
            int(row["size_bytes"]), str(row["sha256"]), row["parent_file_id"],
            int(row["version"]),
        )

    @staticmethod
    def _logical_name(value: str) -> str:
        candidate = Path(value)
        if (
            not value or len(value) > 255 or candidate.is_absolute()
            or len(candidate.parts) != 1 or value in {".", ".."} or "\x00" in value
        ):
            raise ValueError("invalid output logical name")
        return value
