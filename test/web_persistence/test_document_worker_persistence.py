from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from document_activities import DocumentActivities, NormalizeDocumentInput
from file_domain.models import FileResource, NormalizedBlock, NormalizedDocument, SourceLocator
from storage.tenant_file_store import TenantFileStore
from web_domain.services import CommandService
from workspace.file_scope import RunFileWorkspace

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres]


async def test_document_activity_persists_once_and_replays_compact_ref(
    tmp_path, db, account_id, database_url, worker_database_url
) -> None:
    commands = CommandService(database_url)
    conversation_id = UUID(
        commands.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    file_id = uuid4()
    store = TenantFileStore(tmp_path / "objects", max_bytes=1024)
    content = b"document input"
    staged = store.stage(file_id, [content], declared_size=len(content))
    published = store.publish(account_id, file_id, staged)
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
        "ready_at) VALUES (%s,%s,%s,'input','ready','input.pdf','input.pdf',%s,"
        "'application/pdf','utf-8',%s,%s,now())",
        (
            file_id, account_id, conversation_id, published.storage_key,
            published.size_bytes, published.sha256,
        ),
    )
    command = commands.send_message(
        account_id, conversation_id, str(uuid4()), "normalize", file_ids=(file_id,)
    )
    run_id = UUID(command["run_id"])

    class Provider:
        def parse(self, resource: FileResource) -> NormalizedDocument:
            locator = SourceLocator(resource.file_id, resource.logical_name, page=1)
            return NormalizedDocument(
                resource,
                (NormalizedBlock("paragraph", "normalized evidence", locator),),
                metadata={"adapter": "fake-docling", "truncated": False},
            )

    workspace = RunFileWorkspace(worker_database_url, store, tmp_path / "document-runs")
    activities = DocumentActivities(worker_database_url, workspace, Provider())
    request = NormalizeDocumentInput(
        1, str(account_id), str(run_id), str(file_id), f"document:{run_id}:{file_id}"
    )

    first = await activities.normalize_document(request)
    replay = await activities.normalize_document(request)

    assert replay == first
    assert set(first) == {
        "schema_version", "run_id", "file_id", "document_ref",
        "block_count", "table_count", "truncated",
    }
    row = db.execute(
        "SELECT normalized_document,block_count FROM normalized_documents WHERE run_id=%s",
        (run_id,),
    ).fetchone()
    assert row[0]["blocks"][0]["text"] == "normalized evidence"
    assert "local_path" not in row[0]["resource"]
    assert row[1] == 1
