from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from conversation_domain.commands import CommandService
from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from web_domain.errors import ResourceNotFound
from web_domain.file_services import FileService
from workspace.file_scope import RunFileScope, RunFileWorkspace

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_prior_pdf_selected_once_and_frozen_on_retry(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    commands = CommandService(database_url)
    conversation_id = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    files = FileService(database_url, store, max_bytes=1024)
    pdf = b"%PDF-1.4\nP0 test\n%%EOF\n"
    created = files.create_upload(
        account_id, conversation_id, str(uuid4()), "paper.pdf", len(pdf),
        "application/pdf", None,
    )
    file_id = UUID(created["file"]["file_id"])

    async def chunks():
        yield pdf

    await files.upload_content(account_id, file_id, chunks())
    first = UUID(commands.send_message(
        account_id, conversation_id, str(uuid4()), "first", file_ids=(file_id,)
    )["run_id"])
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, first)
    worker.complete_run(account_id, first, "done")
    candidates = files.list_candidates(account_id, conversation_id)["items"]
    assert any(item["file_id"] == str(file_id) for item in candidates)

    second = UUID(commands.send_message(
        account_id, conversation_id, str(uuid4()), "second", file_ids=(file_id,)
    )["run_id"])
    scope = RunFileWorkspace(worker_database_url, store, tmp_path / "execution")
    with scope.prepare(account_id, second) as prepared:
        assert (prepared.inputs_root / "paper.pdf").read_bytes() == pdf
    worker.start_run(account_id, second)
    worker.fail_run(account_id, second, "model_error")
    retry = UUID(commands.retry_run(account_id, second, str(uuid4()))["run_id"])
    assert db.execute(
        "SELECT file_id FROM run_files WHERE run_id=%s AND direction='input'", (retry,)
    ).fetchone()[0] == file_id
    with scope.prepare(account_id, retry) as prepared:
        assert (prepared.inputs_root / "paper.pdf").read_bytes() == pdf
    assert db.execute(
        "SELECT count(*) FROM message_files WHERE file_id=%s AND role='input'", (file_id,)
    ).fetchone()[0] == 2


def test_published_docx_is_later_input_without_rewriting_origin(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    commands = CommandService(database_url)
    conversation_id = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    first = UUID(commands.send_message(account_id, conversation_id, str(uuid4()), "make")['run_id'])
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "result.docx").write_bytes(b"docx-result")
    publisher = OutputPublisher(worker_database_url, store)
    published = publisher.publish(
        RunFileScope(first, tmp_path / "inputs", tmp_path / "scratch", outputs, ()),
        "docx:1", "result.docx",
    )
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, first)
    worker.complete_run(account_id, first, "done")
    second = UUID(commands.send_message(
        account_id, conversation_id, str(uuid4()), "read", file_ids=(published.file_id,)
    )['run_id'])
    row = db.execute(
        "SELECT sf.purpose,rf.direction,sf.file_id FROM run_files rf "
        "JOIN stored_files sf ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
        "WHERE rf.run_id=%s", (second,),
    ).fetchone()
    assert row == ("output", "input", published.file_id)
    with RunFileWorkspace(worker_database_url, store, tmp_path / "execution").prepare(
        account_id, second
    ) as prepared:
        assert (prepared.inputs_root / "result.docx").read_bytes() == b"docx-result"


def test_unpublished_and_cross_account_output_are_inaccessible(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    commands = CommandService(database_url)
    conversation_id = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(commands.send_message(account_id, conversation_id, str(uuid4()), "make")['run_id'])
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    files = FileService(database_url, store, max_bytes=1024)
    staging_id = uuid4()
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,source_run_id,purpose,status,"
        "original_name,display_name) VALUES (%s,%s,%s,%s,'output','uploading','x.md','x.md')",
        (staging_id, account_id, conversation_id, run_id),
    )
    with pytest.raises(ResourceNotFound):
        files.download(account_id, staging_id)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "report.md").write_text("# report")
    published = OutputPublisher(worker_database_url, store).publish(
        RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ()),
        "report:1", "report.md",
    )
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(ResourceNotFound):
        files.download(other, published.file_id)
    other_conversation = UUID(commands.create_conversation(other, str(uuid4()))["conversation_id"])
    with pytest.raises(ResourceNotFound):
        commands.send_message(other, other_conversation, str(uuid4()), "inject", file_ids=(published.file_id,))


def test_publish_recovers_after_object_write_before_database_registration(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    commands = CommandService(database_url)
    conversation_id = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(commands.send_message(account_id, conversation_id, str(uuid4()), "make")['run_id'])
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "report.md").write_text("# stable")
    scope = RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ())

    class FailAfterObjectWrite(TenantFileStore):
        fail_once = True

        def publish(self, account_id, file_id, staged):
            result = super().publish(account_id, file_id, staged)
            if self.fail_once:
                self.fail_once = False
                raise RuntimeError("simulated database registration outage")
            return result

    store = FailAfterObjectWrite(tmp_path / "store", max_bytes=1024)
    publisher = OutputPublisher(worker_database_url, store)
    with pytest.raises(RuntimeError, match="simulated"):
        publisher.publish(scope, "research:markdown", "report.md")
    assert db.execute(
        "SELECT status FROM output_publish_operations WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == "pending"
    assert db.execute("SELECT count(*) FROM run_files WHERE run_id=%s", (run_id,)).fetchone()[0] == 0
    first = publisher.publish(scope, "research:markdown", "report.md")
    replay = publisher.publish(scope, "research:markdown", "report.md")
    assert first.file_id == replay.file_id and replay.deduplicated
    assert db.execute(
        "SELECT count(*) FROM run_files WHERE run_id=%s AND direction='output'", (run_id,)
    ).fetchone()[0] == 1
    assert db.execute(
        "SELECT status FROM output_publish_operations WHERE run_id=%s", (run_id,)
    ).fetchone()[0] == "completed"
