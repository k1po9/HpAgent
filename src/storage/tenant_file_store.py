"""Tenant file storage for immutable Web uploads (FILE-P0-02).

This adapter is deliberately separate from the small-text ``LocalFileStore``.
It never accepts a client-controlled path and never buffers a whole object.
"""
from __future__ import annotations

import codecs
import hashlib
import os
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable
from uuid import UUID


class FileStoreError(Exception):
    code = "file_storage_failed"


class FileTooLarge(FileStoreError):
    code = "file_too_large"


class FileHashMismatch(FileStoreError):
    code = "file_hash_mismatch"


class FileEncodingUnsupported(FileStoreError):
    code = "file_encoding_unsupported"


@dataclass(frozen=True)
class StagedFile:
    staging_key: str
    size_bytes: int
    sha256: str
    encoding: str


@dataclass(frozen=True)
class PublishedFile:
    storage_key: str
    size_bytes: int
    sha256: str


class TenantFileStore:
    """Streaming, generated-key-only storage rooted outside Git workspaces."""

    def __init__(self, root: Path | str, *, max_bytes: int) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes
        self.staging_root = self.root / "staging"
        self.objects_root = self.root / "accounts"
        for directory in (self.root, self.staging_root, self.objects_root):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)
        if self.staging_root.resolve().is_relative_to(self.objects_root.resolve()) or (
            self.objects_root.resolve().is_relative_to(self.staging_root.resolve())
        ):
            raise ValueError("staging and object roots must not overlap")

    @staticmethod
    def storage_key(account_id: UUID, file_id: UUID) -> str:
        return f"accounts/{account_id}/objects/{file_id}/blob"

    @staticmethod
    def staging_key(file_id: UUID) -> str:
        return f"staging/{file_id}.part"

    def stage(
        self,
        file_id: UUID,
        chunks: Iterable[bytes],
        *,
        declared_size: int,
        declared_sha256: str | None = None,
    ) -> StagedFile:
        """Write a complete UTF-8 upload to an exclusive ``.part`` file."""
        if declared_size < 0 or declared_size > self.max_bytes:
            raise FileTooLarge()
        key = self.staging_key(file_id)
        path = self._key_path(key)
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        total = 0
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise TypeError("upload chunks must be bytes")
                total += len(chunk)
                if total > self.max_bytes:
                    raise FileTooLarge()
                if b"\x00" in chunk:
                    raise FileEncodingUnsupported()
                decoder.decode(chunk, final=False)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            decoder.decode(b"", final=True)
            if total != declared_size:
                raise FileStoreError("declared size does not match received bytes")
            actual_hash = digest.hexdigest()
            if declared_sha256 and actual_hash != declared_sha256:
                raise FileHashMismatch()
            os.fsync(fd)
            return StagedFile(key, total, actual_hash, "utf-8")
        except UnicodeDecodeError as exc:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise FileEncodingUnsupported() from exc
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        finally:
            os.close(fd)

    def publish(self, account_id: UUID, file_id: UUID, staged: StagedFile) -> PublishedFile:
        source = self._key_path(staged.staging_key)
        key = self.storage_key(account_id, file_id)
        target = self._key_path(key)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.parent.chmod(0o700)
        if target.exists():
            stat = target.stat(follow_symlinks=False)
            if stat.st_size != staged.size_bytes:
                raise FileStoreError("published object conflicts with staged upload")
            source.unlink(missing_ok=True)
            return PublishedFile(key, staged.size_bytes, staged.sha256)
        os.replace(source, target)
        target.chmod(0o600)
        self._fsync_directory(target.parent)
        return PublishedFile(key, staged.size_bytes, staged.sha256)

    async def stage_async(
        self,
        file_id: UUID,
        chunks: AsyncIterable[bytes],
        *,
        declared_size: int,
        declared_sha256: str | None = None,
    ) -> StagedFile:
        """Async-iterator variant used by the ASGI streaming upload endpoint."""
        if declared_size < 0 or declared_size > self.max_bytes:
            raise FileTooLarge()
        key = self.staging_key(file_id)
        path = self._key_path(key)
        digest = hashlib.sha256()
        decoder = codecs.getincrementaldecoder("utf-8")("strict")
        total = 0
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags, 0o600)
        try:
            async for chunk in chunks:
                total += len(chunk)
                if total > self.max_bytes:
                    raise FileTooLarge()
                if b"\x00" in chunk:
                    raise FileEncodingUnsupported()
                decoder.decode(chunk, final=False)
                digest.update(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(fd, view)
                    view = view[written:]
            decoder.decode(b"", final=True)
            if total != declared_size:
                raise FileStoreError("declared size does not match received bytes")
            actual_hash = digest.hexdigest()
            if declared_sha256 and actual_hash != declared_sha256:
                raise FileHashMismatch()
            os.fsync(fd)
            return StagedFile(key, total, actual_hash, "utf-8")
        except UnicodeDecodeError as exc:
            self._unlink_quietly(path)
            raise FileEncodingUnsupported() from exc
        except BaseException:
            self._unlink_quietly(path)
            raise
        finally:
            os.close(fd)

    def open(self, storage_key: str) -> BinaryIO:
        path = self._key_path(storage_key)
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        return os.fdopen(os.open(path, flags), "rb")

    def delete(self, storage_key: str) -> None:
        self._key_path(storage_key).unlink(missing_ok=True)

    def delete_staging(self, file_id: UUID) -> None:
        self._key_path(self.staging_key(file_id)).unlink(missing_ok=True)

    def _key_path(self, key: str) -> Path:
        candidate = Path(key)
        if candidate.is_absolute() or any(part in {"..", ""} for part in candidate.parts):
            raise FileStoreError("invalid storage key")
        path = (self.root / candidate).absolute()
        if not path.is_relative_to(self.root):
            raise FileStoreError("storage key escapes root")
        current = self.root
        for part in candidate.parts:
            current /= part
            if current.is_symlink():
                raise FileStoreError("symbolic links are not allowed")
        return path

    @staticmethod
    def _fsync_directory(directory: Path) -> None:
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def _unlink_quietly(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
