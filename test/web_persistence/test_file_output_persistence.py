from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from web_domain.errors import ResourceNotFound
from web_domain.file_services import FileService
from web_domain.services import CommandService
from workspace.file_scope import RunFileScope

pytestmark = pytest.mark.postgres


def test_output_publish_is_idempotent_and_binds_on_completion(
    tmp_path, db, account_id, database_url, worker_database_url
):
    api = CommandService(database_url)
    conversation_id = UUID(
        api.create_conversation(account_id, str(uuid4()))["conversation_id"]
    )
    command = api.send_message(account_id, conversation_id, str(uuid4()), "create output")
    run_id = UUID(command["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)

    outputs = tmp_path / "run" / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "report.docx").write_bytes(b"immutable-output")
    scope = RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ())
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    publisher = OutputPublisher(worker_database_url, store)

    first = publisher.publish(scope, "output:1", "report.docx")
    replay = publisher.publish(scope, "output:1", "report.docx")
    assert replay.file_id == first.file_id
    assert replay.deduplicated is True
    assert db.execute(
        "SELECT count(*) FROM run_files WHERE run_id=%s AND direction='output'", (run_id,)
    ).fetchone()[0] == 1

    files = FileService(database_url, store, max_bytes=1024)
    with pytest.raises(ResourceNotFound):
        files.download(account_id, first.file_id)

    CommandService(worker_database_url).complete_run(account_id, run_id, "done")
    bound = db.execute(
        "SELECT mf.file_id,mf.role FROM message_files mf JOIN messages m "
        "ON m.message_id=mf.message_id WHERE m.produced_by_run_id=%s", (run_id,)
    ).fetchone()
    assert bound == (first.file_id, "output")
    metadata, stream = files.download(account_id, first.file_id)
    with stream:
        assert stream.read() == b"immutable-output"
    assert metadata["file_id"] == str(first.file_id)


def test_output_publish_rejects_operation_name_mismatch(
    tmp_path, db, account_id, database_url, worker_database_url
):
    api = CommandService(database_url)
    conversation_id = UUID(api.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(api.send_message(account_id, conversation_id, str(uuid4()), "output")["run_id"])
    outputs = tmp_path / "run" / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "a.docx").write_bytes(b"a")
    (outputs / "b.docx").write_bytes(b"b")
    scope = RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ())
    publisher = OutputPublisher(worker_database_url, TenantFileStore(tmp_path / "store", max_bytes=10))
    publisher.publish(scope, "stable-op", "a.docx")
    with pytest.raises(RuntimeError, match="another logical file"):
        publisher.publish(scope, "stable-op", "b.docx")
