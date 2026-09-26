"""Version chains exercise PostgreSQL, the Run publisher, and Workspace CAS."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import psycopg
import pytest

from conversation_domain.commands import CommandService
from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from web_domain.file_services import FileService
from workspace.catalog import WorkspaceCatalog, WorkspaceConflict, WorkspaceVersionConflict
from workspace.file_scope import RunFileScope
from workspace.resources import ResourceDenied, ResourcePolicy

pytestmark = pytest.mark.postgres


def _setup(db, account_id, database_url, worker_database_url, tmp_path):
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    commands = CommandService(database_url)
    source = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    run = UUID(commands.send_message(account_id, source, str(uuid4()), "create")["run_id"])
    publisher = OutputPublisher(worker_database_url, store)
    base = _publish(publisher, tmp_path, run, "initial", b"v1")
    e1 = catalog.save_file(account_id, parent, base.file_id, "E1.txt", "save:e1")
    e2 = catalog.save_file(account_id, parent, base.file_id, "E2.txt", "save:e2")
    return catalog, commands, store, publisher, parent, e1, e2, base


def _publish(publisher, tmp_path, run_id, operation_id, data, parent_file_id=None):
    outputs = tmp_path / str(run_id) / operation_id
    outputs.mkdir(parents=True)
    (outputs / "result.txt").write_bytes(data)
    return publisher.publish(
        RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ()),
        operation_id, "result.txt", parent_file_id=parent_file_id,
    )


def _editing_run(commands, policy, account_id, entry):
    conversation = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    policy.grant(account_id, "conversation", conversation, entry,
                 ["list_metadata", "read_content", "update_content"], False)
    run_id = UUID(commands.send_message(
        account_id, conversation, str(uuid4()), "edit")["run_id"])
    policy.select(account_id, run_id, entry)
    return run_id


def test_shared_initial_bytes_upgrade_and_history(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    catalog, _, store, _, parent, e1, e2, base = _setup(
        db, account_id, database_url, worker_database_url, tmp_path)
    with ThreadPoolExecutor(max_workers=2) as pool:
        upgrades = list(pool.map(lambda _: catalog.upgrade_file(account_id, e1), range(2)))
    assert upgrades[0] == upgrades[1]
    assert any(n["node_id"] == str(e1) and n["destination_id"] ==
               upgrades[0]["destination_id"] for n in catalog.tree(account_id)["nodes"])
    assert catalog.versions(account_id, e2)["current"]["destination_id"] is None
    second = catalog.upgrade_file(account_id, e2)
    assert second["destination_id"] != upgrades[0]["destination_id"]
    assert second["file_id"] == upgrades[0]["file_id"] == str(base.file_id)
    assert db.execute("SELECT count(*) FROM persistent_file_revisions WHERE file_id=%s",
                      (base.file_id,)).fetchone()[0] == 2
    with pytest.raises(psycopg.errors.RaiseException):
        db.execute("UPDATE persistent_file_revisions SET sha256=%s "
                   "WHERE destination_id=%s AND revision=1",
                   ("0" * 64, UUID(upgrades[0]["destination_id"])))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("UPDATE persistent_file_destinations SET current_revision=9 "
                   "WHERE destination_id=%s", (UUID(upgrades[0]["destination_id"]),))
    before = catalog.versions(account_id, e1)
    catalog.move(account_id, e1, parent, "renamed.txt")
    assert catalog.versions(account_id, e1) == before
    with FileService(database_url, store, max_bytes=1024).download(
        account_id, base.file_id)[1] as stream:
        assert stream.read() == b"v1"


def test_cross_conversation_cas_replay_and_published_recovery(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    catalog, commands, store, publisher, parent, e1, _, base = _setup(
        db, account_id, database_url, worker_database_url, tmp_path)
    catalog.upgrade_file(account_id, e1)
    policy = ResourcePolicy(database_url)
    run_a = _editing_run(commands, policy, account_id, e1)
    run_b = _editing_run(commands, policy, account_id, e1)
    a = _publish(publisher, tmp_path, run_a, "output:a", b"v2", base.file_id)
    b = _publish(publisher, tmp_path, run_b, "output:b", b"conflict", base.file_id)
    assert db.execute("SELECT count(*) FROM stored_files").fetchone()[0] == 3
    # Publication is already durable; a restarted save service resumes without publishing again.
    catalog = WorkspaceCatalog(database_url)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(catalog.update_file, account_id, e1, run_id, output.file_id,
                               1, base.sha256, operation)
                   for run_id, output, operation in
                   ((run_a, a, "version:a"), (run_b, b, "version:b"))]
        results = []
        for future in futures:
            try:
                results.append(future.result())
            except WorkspaceVersionConflict as exc:
                assert exc.current["revision"] == 2
    assert len(results) == 1
    winner = results[0]
    loser = b if winner["file_id"] == str(a.file_id) else a
    assert db.execute("SELECT count(*) FROM persistent_file_revisions WHERE revision=2") \
        .fetchone()[0] == 1
    assert db.execute("SELECT status FROM stored_files WHERE file_id=%s",
                      (loser.file_id,)).fetchone()[0] == "ready"
    alternate = catalog.save_file(account_id, parent, loser.file_id,
                                  "conflict-output.txt", "save:conflict")
    assert catalog.versions(account_id, alternate)["current"]["file_id"] == str(loser.file_id)
    assert db.execute("SELECT parent_file_id FROM stored_files WHERE file_id=%s",
                      (a.file_id,)).fetchone()[0] == base.file_id
    assert db.execute("SELECT fixed_file_id,fixed_revision FROM run_resource_candidates "
                      "WHERE run_id=%s AND node_id=%s", (run_b, e1)).fetchone() == (
                          base.file_id, 1)
    with FileService(database_url, store, max_bytes=1024).download(
        account_id, base.file_id)[1] as stream:
        assert stream.read() == b"v1"
    run_c = _editing_run(commands, policy, account_id, e1)
    assert db.execute("SELECT fixed_revision FROM run_resource_candidates WHERE run_id=%s",
                      (run_c,)).fetchone()[0] == 2
    c = _publish(publisher, tmp_path, run_c, "output:c", b"v3")
    current = catalog.versions(account_id, e1)["current"]
    third = catalog.update_file(account_id, e1, run_c, c.file_id, 2,
                                current["sha256"], "version:c")
    assert third["revision"] == 3
    old_run, old_output, old_operation = (
        (run_a, a, "version:a") if winner["file_id"] == str(a.file_id)
        else (run_b, b, "version:b"))
    assert catalog.update_file(account_id, e1, old_run, old_output.file_id, 1,
                               base.sha256, old_operation) == winner
    with pytest.raises(WorkspaceConflict):
        catalog.update_file(account_id, e1, old_run, c.file_id, 1,
                            base.sha256, old_operation)
    assert [r["revision"] for r in catalog.versions(account_id, e1)["revisions"]] == [3, 2, 1]


def test_cancelled_or_unauthorized_run_cannot_commit(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    catalog, commands, _, publisher, _, e1, _, base = _setup(
        db, account_id, database_url, worker_database_url, tmp_path)
    catalog.upgrade_file(account_id, e1)
    policy = ResourcePolicy(database_url)
    run = _editing_run(commands, policy, account_id, e1)
    output = _publish(publisher, tmp_path, run, "output:cancel", b"new")
    commands.cancel_run(account_id, run, str(uuid4()))
    with pytest.raises(ResourceDenied):
        catalog.update_file(account_id, e1, run, output.file_id, 1,
                            base.sha256, "version:cancel")
    unauthorized_conversation = UUID(commands.create_conversation(
        account_id, str(uuid4()))["conversation_id"])
    policy.grant(account_id, "conversation", unauthorized_conversation, e1,
                 ["list_metadata", "read_content"], False)
    unauthorized_run = UUID(commands.send_message(
        account_id, unauthorized_conversation, str(uuid4()), "edit")["run_id"])
    policy.select(account_id, unauthorized_run, e1)
    unauthorized_output = _publish(publisher, tmp_path, unauthorized_run,
                                   "output:unauthorized", b"unauthorized")
    with pytest.raises(ResourceDenied):
        catalog.update_file(account_id, e1, unauthorized_run,
                            unauthorized_output.file_id, 1, base.sha256,
                            "version:unauthorized")
    assert catalog.versions(account_id, e1)["current"]["revision"] == 1
