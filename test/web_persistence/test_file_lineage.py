from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from web_domain.file_services import FileService
from web_domain.services import CommandService
from workspace.file_scope import RunFileScope

pytestmark = pytest.mark.postgres


def test_database_versions_branch_lineage_and_api_returns_root_first(
    tmp_path, db, account_id, database_url, worker_database_url
):
    api = CommandService(database_url)
    conversation_id = UUID(api.create_conversation(account_id, str(uuid4()))["conversation_id"])
    root_id = uuid4()
    root_content = b"root"
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    staged = store.stage(root_id, [root_content], declared_size=len(root_content))
    root = store.publish(account_id, root_id, staged)
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,ready_at) "
        "VALUES (%s,%s,%s,'input','ready','root.docx','root.docx',%s,'application/docx',"
        "'binary',%s,%s,now())",
        (root_id, account_id, conversation_id, root.storage_key, root.size_bytes, root.sha256),
    )
    run_id = UUID(api.send_message(
        account_id, conversation_id, str(uuid4()), "version it", file_ids=(root_id,)
    )["run_id"])
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, run_id)
    outputs = tmp_path / "run" / "outputs"
    outputs.mkdir(parents=True)
    scope = RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ())
    publisher = OutputPublisher(worker_database_url, store)

    (outputs / "root-v2.docx").write_bytes(b"version two")
    version2 = publisher.publish(
        scope, "lineage:v2", "root-v2.docx", parent_file_id=root_id
    )
    (outputs / "root-v3.docx").write_bytes(b"version three")
    version3 = publisher.publish(
        scope, "lineage:v3", "root-v3.docx", parent_file_id=version2.file_id
    )
    assert (version2.version, version3.version) == (2, 3)

    assert worker.complete_run(account_id, run_id, "versions complete") is True
    assert db.execute(
        "SELECT count(*) FROM message_files mf JOIN messages m USING(message_id) "
        "WHERE m.produced_by_run_id=%s AND mf.role='output'", (run_id,)
    ).fetchone()[0] == 2
    files = FileService(database_url, store, max_bytes=1024)
    lineage = files.lineage(account_id, version3.file_id)
    assert [item["file_id"] for item in lineage] == [
        str(root_id), str(version2.file_id), str(version3.file_id)
    ]
    assert [item["version"] for item in lineage] == [1, 2, 3]


def test_database_ignores_caller_supplied_child_version(
    tmp_path, db, account_id, database_url
):
    api = CommandService(database_url)
    conversation_id = UUID(api.create_conversation(account_id, str(uuid4()))["conversation_id"])
    parent_id, child_id = uuid4(), uuid4()
    for file_id, name in ((parent_id, "parent.docx"),):
        db.execute(
            "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
            "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,ready_at) "
            "VALUES (%s,%s,%s,'input','ready',%s,%s,%s,'application/docx','binary',1,%s,now())",
            (file_id, account_id, conversation_id, name, name,
             f"accounts/{account_id}/objects/{file_id}/blob", "a" * 64),
        )
    row = db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
        "parent_file_id,version,ready_at) VALUES (%s,%s,%s,'output','ready','child.docx',"
        "'child.docx',%s,'application/docx','binary',1,%s,%s,999,now()) RETURNING version",
        (child_id, account_id, conversation_id,
         f"accounts/{account_id}/objects/{child_id}/blob", "b" * 64, parent_id),
    ).fetchone()
    assert row[0] == 2
    with pytest.raises(psycopg.errors.RaiseException, match="lineage is immutable"):
        db.execute(
            "UPDATE stored_files SET parent_file_id=NULL WHERE file_id=%s", (child_id,)
        )
