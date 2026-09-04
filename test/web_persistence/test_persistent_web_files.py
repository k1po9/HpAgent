from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest

from file_domain.approvals import ApprovalNotGranted, FileActionApprovalService
from file_domain.persistent import DestinationChanged, PersistentWebFileService
from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from web_domain.services import CommandService
from workspace.file_scope import RunFileScope

pytestmark = pytest.mark.postgres


def _run(account_id, database_url, worker_database_url):
    api = CommandService(database_url)
    conversation_id = UUID(api.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(api.send_message(
        account_id, conversation_id, str(uuid4()), "save persistent report"
    )["run_id"])
    CommandService(worker_database_url).start_run(account_id, run_id)
    return conversation_id, run_id


def _source(tmp_path: Path, worker_database_url, store, run_id, name, content):
    root = tmp_path / str(uuid4())
    outputs = root / "outputs"
    outputs.mkdir(parents=True)
    (outputs / name).write_bytes(content)
    scope = RunFileScope(run_id, root / "inputs", root / "scratch", outputs, ())
    return OutputPublisher(worker_database_url, store).publish(
        scope, f"source:{name}:{uuid4()}", name, "text/markdown", encoding="utf-8"
    )


def test_create_retry_and_overwrite_approval_are_exact_and_tenant_scoped(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    _conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    store = TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024)
    first_source = _source(tmp_path, worker_database_url, store, run_id, "one.md", b"one")
    second_source = _source(tmp_path, worker_database_url, store, run_id, "two.md", b"two")
    service = PersistentWebFileService(worker_database_url, store)

    created = service.save(
        account_id, run_id, "persist-create", "research/agent-memory.md",
        first_source.file_id,
    )
    replay = service.save(
        account_id, run_id, "persist-create", "research/agent-memory.md",
        first_source.file_id,
    )
    assert (created.status, created.revision, replay.deduplicated) == (
        "completed", 1, True,
    )
    pending = service.save(
        account_id, run_id, "persist-overwrite", "research/agent-memory.md",
        second_source.file_id,
    )
    assert pending.status == "approval_required"
    rows = db.execute(
        "SELECT d.current_revision,d.current_file_id,count(r.revision) AS revisions "
        "FROM hpagent.persistent_file_destinations d "
        "JOIN hpagent.persistent_file_revisions r USING(destination_id) "
        "WHERE d.account_id=%s AND d.logical_path=%s "
        "GROUP BY d.current_revision,d.current_file_id",
        (account_id, "research/agent-memory.md"),
    ).fetchone()
    assert rows == (1, created.file_id, 1)
    approval = db.execute(
        "SELECT arguments_hash,intent FROM hpagent.file_action_approvals "
        "WHERE approval_id=%s", (pending.approval_id,),
    ).fetchone()
    arguments_hash, intent = approval
    assert intent == {
        "logical_path": "research/agent-memory.md",
        "source_file_id": str(second_source.file_id),
        "expected_revision": 1,
        "expected_sha256": first_source.sha256,
        "new_sha256": second_source.sha256,
    }
    assert arguments_hash == service.arguments_hash(intent)

    other = uuid4()
    db.execute("INSERT INTO hpagent.accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(LookupError):
        service.save(
            other, run_id, "cross-tenant", "research/agent-memory.md",
            second_source.file_id,
        )
    _other_conversation_id, other_run_id = _run(
        other, database_url, worker_database_url
    )
    other_source = _source(
        tmp_path, worker_database_url, store, other_run_id, "other.md", b"other"
    )
    other_created = service.save(
        other, other_run_id, "other-create", "research/agent-memory.md",
        other_source.file_id,
    )
    assert (other_created.revision, other_created.sha256) == (1, other_source.sha256)
    assert other_created.file_id != created.file_id


def test_recoverable_grant_cas_and_immutable_history(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    _conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    store = TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024)
    first = _source(tmp_path, worker_database_url, store, run_id, "base.md", b"base")
    update = _source(tmp_path, worker_database_url, store, run_id, "update.md", b"update")
    service = PersistentWebFileService(worker_database_url, store)
    original = service.save(account_id, run_id, "create", "report.md", first.file_id)
    pending = service.save(account_id, run_id, "overwrite", "report.md", update.file_id)
    approvals = FileActionApprovalService(database_url)
    approvals.decide(account_id, pending.approval_id, "approved", str(uuid4()))

    approval = db.execute(
        "SELECT arguments_hash FROM hpagent.file_action_approvals WHERE approval_id=%s",
        (pending.approval_id,),
    ).fetchone()
    worker_approvals = FileActionApprovalService(worker_database_url)
    worker_approvals.consume(
        account_id, run_id, "overwrite", service.TOOL_NAME, approval[0],
        execution_id="overwrite", fencing_token=9,
    )
    with pytest.raises(ApprovalNotGranted):
        worker_approvals.consume(
            account_id, run_id, "overwrite", service.TOOL_NAME, approval[0],
            execution_id="another-operation", fencing_token=9,
        )

    # Simulate a crash after grant binding but before the persistent write.
    result = service.execute_approved_overwrite(
        account_id, run_id, "overwrite", execution_id="overwrite", fencing_token=9
    )
    recovered = service.execute_approved_overwrite(
        account_id, run_id, "overwrite", execution_id="overwrite", fencing_token=9
    )
    assert (result.revision, recovered.revision, recovered.deduplicated) == (2, 2, True)
    with pytest.raises(ApprovalNotGranted):
        service.execute_approved_overwrite(
            account_id, run_id, "overwrite", execution_id="overwrite", fencing_token=10
        )
    revisions = db.execute(
        "SELECT revision,file_id FROM hpagent.persistent_file_revisions "
        "ORDER BY revision"
    ).fetchall()
    assert revisions == [(1, original.file_id), (2, result.file_id)]
    contents = []
    for _, file_id in revisions:
        storage_key = db.execute(
            "SELECT storage_key FROM hpagent.stored_files WHERE file_id=%s", (file_id,)
        ).fetchone()[0]
        with store.open(storage_key) as reader:
            contents.append(reader.read())
    assert contents == [b"base", b"update"]


def test_destination_change_after_approval_fails_closed(
    tmp_path, db, account_id, database_url, worker_database_url,
):
    _conversation_id, run_id = _run(account_id, database_url, worker_database_url)
    store = TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024)
    first = _source(tmp_path, worker_database_url, store, run_id, "first.md", b"first")
    update = _source(tmp_path, worker_database_url, store, run_id, "next.md", b"next")
    service = PersistentWebFileService(worker_database_url, store)
    service.save(account_id, run_id, "create", "report.md", first.file_id)
    pending = service.save(account_id, run_id, "overwrite", "report.md", update.file_id)
    FileActionApprovalService(database_url).decide(
        account_id, pending.approval_id, "approved", str(uuid4())
    )
    db.execute(
        "UPDATE hpagent.persistent_file_destinations SET current_revision=2,"
        "last_operation_id='concurrent' WHERE account_id=%s AND logical_path='report.md'",
        (account_id,),
    )
    with pytest.raises(DestinationChanged, match="changed after approval"):
        service.execute_approved_overwrite(
            account_id, run_id, "overwrite", execution_id="overwrite", fencing_token=3
        )
