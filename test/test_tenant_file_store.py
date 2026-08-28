from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from uuid import uuid4

import pytest

from storage.tenant_file_store import (
    FileEncodingUnsupported,
    FileHashMismatch,
    FileStoreError,
    FileTooLarge,
    TenantFileStore,
)


def test_streaming_stage_publish_and_read(tmp_path: Path) -> None:
    account_id, file_id = uuid4(), uuid4()
    content = "第一行\nsecond\n".encode()
    store = TenantFileStore(tmp_path / "files", max_bytes=1024)
    staged = store.stage(
        file_id,
        [content[:2], content[2:7], content[7:]],
        declared_size=len(content),
        declared_sha256=hashlib.sha256(content).hexdigest(),
    )
    published = store.publish(account_id, file_id, staged)

    assert published.storage_key == store.storage_key(account_id, file_id)
    with store.open(published.storage_key) as stream:
        assert stream.read() == content
    assert ((tmp_path / "files").stat().st_mode & 0o777) == 0o700


async def test_async_streaming_stage_never_requires_a_complete_body(tmp_path: Path) -> None:
    content = "分块\ncontent\n".encode()

    async def chunks():
        for byte in content:
            yield bytes([byte])

    store = TenantFileStore(tmp_path / "files", max_bytes=1024)
    staged = await store.stage_async(
        uuid4(), chunks(), declared_size=len(content),
        declared_sha256=hashlib.sha256(content).hexdigest(),
    )
    assert staged.size_bytes == len(content)
    assert staged.sha256 == hashlib.sha256(content).hexdigest()


async def test_async_upload_cancellation_removes_partial_file(tmp_path: Path) -> None:
    file_id = uuid4()

    async def chunks():
        yield b"partial"
        raise asyncio.CancelledError()

    store = TenantFileStore(tmp_path / "files", max_bytes=1024)
    with pytest.raises(asyncio.CancelledError):
        await store.stage_async(file_id, chunks(), declared_size=100)
    assert not (store.root / store.staging_key(file_id)).exists()


@pytest.mark.parametrize(
    ("chunks", "size", "digest", "error"),
    [
        ([b"abcd"], 4, None, FileTooLarge),
        ([b"a\x00b"], 3, None, FileEncodingUnsupported),
        ([b"\xff"], 1, None, FileEncodingUnsupported),
        ([b"abc"], 3, "0" * 64, FileHashMismatch),
        ([b"abc"], 4, None, FileStoreError),
    ],
)
def test_rejected_stage_removes_partial_file(
    tmp_path: Path, chunks: list[bytes], size: int, digest: str | None, error: type[Exception]
) -> None:
    store = TenantFileStore(tmp_path / "files", max_bytes=3)
    file_id = uuid4()
    with pytest.raises(error):
        store.stage(file_id, chunks, declared_size=size, declared_sha256=digest)
    assert not (store.root / store.staging_key(file_id)).exists()


def test_storage_keys_cannot_escape_or_follow_symlinks(tmp_path: Path) -> None:
    store = TenantFileStore(tmp_path / "files", max_bytes=10)
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(FileStoreError):
        store.open("../outside/secret")
    with pytest.raises(FileStoreError):
        store.open("link/secret")
