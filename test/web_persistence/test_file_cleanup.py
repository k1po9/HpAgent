from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from storage.tenant_file_store import TenantFileStore
from web_domain.file_cleanup import FileCleanupService
from web_domain.file_services import FileService
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def _conversation(database_url: str, account_id: UUID) -> UUID:
    return UUID(CommandService(database_url).create_conversation(
        account_id, str(uuid4())
    )["conversation_id"])


def test_expired_crashed_upload_removes_staging_and_canonical_orphan(
    db, account_id, database_url, worker_database_url, tmp_path,
) -> None:
    conversation_id = _conversation(database_url, account_id)
    store = TenantFileStore(tmp_path / "objects", max_bytes=1024)
    files = FileService(database_url, store, max_bytes=1024)
    created = files.create_upload(
        account_id, conversation_id, str(uuid4()),
        "service.log", 5, "text/plain", None,
    )
    file_id = UUID(created["file"]["file_id"])
    staged = store.stage(file_id, [b"hello"], declared_size=5)
    canonical = store.root / store.storage_key(account_id, file_id)
    canonical.parent.mkdir(parents=True)
    canonical.write_bytes(b"crashed-after-publish")
    db.execute(
        """
        UPDATE stored_files
        SET created_at = created_at - interval '1 day',
            expires_at = clock_timestamp() - interval '1 second'
        WHERE file_id=%s
        """,
        (file_id,),
    )

    result = FileCleanupService(worker_database_url, store).cleanup_once()

    assert result.deleted == 1
    assert not (store.root / staged.staging_key).exists()
    assert not canonical.exists()
    row = db.execute(
        "SELECT status,storage_key,expires_at FROM stored_files WHERE file_id=%s",
        (file_id,),
    ).fetchone()
    assert row == ("deleted", None, None)


@pytest.mark.asyncio
async def test_bound_ready_file_is_never_claimed_by_ttl_cleanup(
    db, account_id, database_url, worker_database_url, tmp_path,
) -> None:
    commands = CommandService(database_url)
    conversation_id = _conversation(database_url, account_id)
    store = TenantFileStore(tmp_path / "objects", max_bytes=1024)
    files = FileService(database_url, store, max_bytes=1024)
    created = files.create_upload(
        account_id, conversation_id, str(uuid4()),
        "service.log", 5, "text/plain", None,
    )
    file_id = UUID(created["file"]["file_id"])

    async def chunks():
        yield b"hello"

    await files.upload_content(account_id, file_id, chunks())
    commands.send_message(
        account_id, conversation_id, str(uuid4()), "analyze", file_ids=(file_id,)
    )
    db.execute(
        """
        UPDATE stored_files
        SET created_at = created_at - interval '1 day',
            expires_at = clock_timestamp() - interval '1 second'
        WHERE file_id=%s
        """,
        (file_id,),
    )

    result = FileCleanupService(worker_database_url, store).cleanup_once()

    assert result.claimed == 0
    assert (store.root / store.storage_key(account_id, file_id)).exists()
    assert db.execute(
        "SELECT status FROM stored_files WHERE file_id=%s", (file_id,)
    ).fetchone()[0] == "ready"
