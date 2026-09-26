from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest

from conversation_domain.commands import CommandService
from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from web_domain.file_cleanup import FileCleanupService
from web_domain.file_services import FileService
from workspace.catalog import WorkspaceCatalog, WorkspaceConflict, WorkspaceNotFound
from workspace.file_scope import RunFileScope

pytestmark = pytest.mark.postgres


def _node(tree, name):
    return UUID(next(node["node_id"] for node in tree["nodes"] if node["name"] == name))


def test_default_workspace_is_once_only_and_names_are_shared(db, account_id, database_url):
    catalog = WorkspaceCatalog(database_url)
    first = catalog.initialize(account_id)
    assert {n["name"] for n in first["nodes"]} == {"", "资料", "任务", "成果"}
    assert catalog.initialize(account_id) == first
    root = UUID(first["root_id"])
    info = _node(first, "资料")
    catalog.remove(account_id, info)
    assert "资料" not in {n["name"] for n in catalog.initialize(account_id)["nodes"]}
    catalog.create_directory(account_id, root, "Report")
    with pytest.raises(psycopg.errors.UniqueViolation):
        catalog.create_directory(account_id, root, "report")


def test_tree_rejects_cycle_cross_account_and_nonempty_delete(
    db, account_id, database_url,
):
    catalog = WorkspaceCatalog(database_url)
    root = UUID(catalog.initialize(account_id)["root_id"])
    a = catalog.create_directory(account_id, root, "A")
    b = catalog.create_directory(account_id, a, "B")
    with pytest.raises(psycopg.errors.RaiseException):
        catalog.move(account_id, a, b, "A")
    with pytest.raises(psycopg.errors.RaiseException):
        catalog.remove(account_id, a)
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    other_root = UUID(catalog.initialize(other)["root_id"])
    with pytest.raises((psycopg.errors.ForeignKeyViolation, psycopg.errors.RaiseException)):
        catalog.move(account_id, a, other_root, "A")
    catalog.move(account_id, b, root, "B")
    catalog.remove(account_id, a)


def test_save_move_remove_are_zero_copy_and_idempotent(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    root, info = UUID(tree["root_id"]), _node(tree, "资料")
    results = _node(tree, "成果")
    commands = CommandService(database_url)
    conversation = UUID(commands.create_conversation(account_id, str(uuid4()))["conversation_id"])
    run_id = UUID(commands.send_message(account_id, conversation, str(uuid4()), "make")['run_id'])
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "report.md").write_text("# report")
    published = OutputPublisher(worker_database_url, store).publish(
        RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ()),
        "report:1", "report.md",
    )
    before = db.execute("SELECT count(*) FROM stored_files").fetchone()[0]
    first = catalog.save_file(account_id, info, published.file_id, "Report.md", "save:1")
    assert catalog.save_file(account_id, info, published.file_id, "Report.md", "save:1") == first
    with pytest.raises(WorkspaceConflict):
        catalog.save_file(account_id, results, published.file_id, "Report.md", "save:1")
    with pytest.raises(psycopg.errors.UniqueViolation):
        catalog.create_directory(account_id, info, "report.md")
    second = catalog.save_file(account_id, results, published.file_id, "Another.md", "save:2")
    catalog.move(account_id, first, root, "Moved.md")
    assert db.execute("SELECT count(*) FROM stored_files").fetchone()[0] == before
    assert db.execute(
        "SELECT count(*) FROM workspace_nodes WHERE file_id=%s AND deleted_at IS NULL",
        (published.file_id,),
    ).fetchone()[0] == 2
    catalog.remove(account_id, first)
    assert db.execute("SELECT file_id FROM workspace_nodes WHERE node_id=%s", (second,)).fetchone()[0] == published.file_id
    with FileService(database_url, store, max_bytes=1024).download(account_id, published.file_id)[1] as stream:
        assert stream.read() == b"# report"


def test_gc_keeps_revisions_and_active_entries(db, account_id, database_url, worker_database_url, tmp_path):
    catalog = WorkspaceCatalog(database_url)
    root = UUID(catalog.initialize(account_id)["root_id"])
    conversation = UUID(CommandService(database_url).create_conversation(account_id, str(uuid4()))["conversation_id"])
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    file_id = uuid4()
    staged = store.stage(file_id, [b"saved"], declared_size=5)
    published = store.publish(account_id, file_id, staged)
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
        "ready_at,expires_at) VALUES (%s,%s,%s,'input','ready','saved.md','saved.md',"
        "%s,'text/markdown','utf-8',%s,%s,now(),now())",
        (file_id, account_id, conversation, published.storage_key,
         published.size_bytes, published.sha256),
    )
    entry = catalog.save_file(account_id, root, file_id, "saved.md", "save:gc")
    db.execute("UPDATE stored_files SET expires_at=now() WHERE file_id=%s", (file_id,))
    assert FileCleanupService(worker_database_url, store).cleanup_once().claimed == 0
    catalog.remove(account_id, entry)
    # A retained revision independently protects bytes without an active entry.
    destination_id = uuid4()
    with db.transaction():
        db.execute(
            "INSERT INTO persistent_file_destinations(destination_id,account_id,"
            "current_revision,current_file_id,current_sha256) "
            "VALUES (%s,%s,1,%s,%s)",
            (destination_id, account_id, file_id, published.sha256),
        )
        db.execute(
            "INSERT INTO persistent_file_revisions(account_id,destination_id,revision,"
            "file_id,sha256,operation_id) VALUES (%s,%s,1,%s,%s,'old')",
            (account_id, destination_id, file_id, published.sha256),
        )
    assert FileCleanupService(worker_database_url, store).cleanup_once().claimed == 0


def test_concurrent_moves_cannot_form_cycle(db, account_id, database_url):
    catalog = WorkspaceCatalog(database_url)
    root = UUID(catalog.initialize(account_id)["root_id"])
    a = catalog.create_directory(account_id, root, "A")
    b = catalog.create_directory(account_id, root, "B")
    start = Barrier(2)

    def move(node, parent, name):
        start.wait()
        try:
            catalog.move(account_id, node, parent, name)
            return "moved"
        except psycopg.errors.RaiseException:
            return "cycle_rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        one = pool.submit(move, a, b, "A")
        two = pool.submit(move, b, a, "B")
        assert sorted([one.result(), two.result()]) == ["cycle_rejected", "moved"]


def test_gc_competes_with_save_and_delete_failure_retries(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    catalog = WorkspaceCatalog(database_url)
    root = UUID(catalog.initialize(account_id)["root_id"])
    conversation = UUID(CommandService(database_url).create_conversation(
        account_id, str(uuid4())
    )["conversation_id"])

    class FailOnceStore(TenantFileStore):
        fail = True

        def delete(self, storage_key):
            if self.fail:
                self.fail = False
                raise OSError("temporary file store outage")
            return super().delete(storage_key)

    store = FailOnceStore(tmp_path / "store", max_bytes=1024)
    file_id = uuid4()
    staged = store.stage(file_id, [b"race"], declared_size=4)
    published = store.publish(account_id, file_id, staged)
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
        "created_at,ready_at,expires_at) VALUES "
        "(%s,%s,%s,'input','ready','race.md','race.md',%s,'text/markdown',"
        "'utf-8',%s,%s,now()-interval '1 day',now(),now()-interval '1 second')",
        (file_id, account_id, conversation, published.storage_key,
         published.size_bytes, published.sha256),
    )
    start = Barrier(2)

    def save():
        start.wait()
        try:
            return catalog.save_file(account_id, root, file_id, "race.md", "race:save")
        except (WorkspaceNotFound, psycopg.errors.RaiseException):
            return None

    def collect():
        start.wait()
        return FileCleanupService(worker_database_url, store).cleanup_once()

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(save)
        collected = pool.submit(collect)
        entry_id, first_gc = saved.result(), collected.result()
    row = db.execute("SELECT status,storage_key FROM stored_files WHERE file_id=%s", (file_id,)).fetchone()
    active = db.execute(
        "SELECT count(*) FROM workspace_nodes WHERE file_id=%s AND deleted_at IS NULL",
        (file_id,),
    ).fetchone()[0]
    assert active == (1 if entry_id else 0)
    if active:
        assert row[0] == "ready" and (store.root / published.storage_key).exists()
        assert first_gc.claimed == 0
    else:
        assert row[0] == "deleted"
        assert first_gc.failed == 1
        retried = FileCleanupService(worker_database_url, store).cleanup_once()
        assert retried.deleted == 1
        assert not (store.root / published.storage_key).exists()


def test_physical_delete_failure_retries_without_live_reference(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    class FailOnceStore(TenantFileStore):
        failed = False

        def delete(self, key):
            if not self.failed:
                self.failed = True
                raise OSError("transient storage failure")
            return super().delete(key)

    store = FailOnceStore(tmp_path / "store", max_bytes=1024)
    file_id = uuid4()
    staged = store.stage(file_id, [b"orphan"], declared_size=6)
    published = store.publish(account_id, file_id, staged)
    conversation = UUID(CommandService(database_url).create_conversation(
        account_id, str(uuid4())
    )["conversation_id"])
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
        "created_at,ready_at,expires_at) VALUES "
        "(%s,%s,%s,'input','ready','orphan.md','orphan.md',%s,'text/markdown',"
        "'utf-8',%s,%s,now()-interval '1 day',now(),now()-interval '1 second')",
        (file_id, account_id, conversation, published.storage_key,
         published.size_bytes, published.sha256),
    )
    cleanup = FileCleanupService(worker_database_url, store)
    assert cleanup.cleanup_once().failed == 1
    assert (store.root / published.storage_key).exists()
    assert cleanup.cleanup_once().deleted == 1
    assert not (store.root / published.storage_key).exists()
