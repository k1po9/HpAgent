"""P2 authority exercised through real PostgreSQL, FileStore and Run scope."""
from __future__ import annotations

import asyncio
import threading
from uuid import UUID, uuid4

import pytest

from conversation_domain.commands import CommandService
from file_runtime import FileResourceResolver
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog
from workspace.file_scope import RunFileScopeUnavailable, RunFileWorkspace
from workspace.resources import ResourceDenied, ResourcePolicy, SnapshotLimitExceeded

pytestmark = pytest.mark.postgres


def _file(db, store, account, conversation, name: str, body: bytes) -> UUID:
    file_id = uuid4()
    staged = store.stage(file_id, [body], declared_size=len(body))
    published = store.publish(account, file_id, staged)
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,conversation_id,purpose,status,"
        "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,"
        "ready_at,expires_at) VALUES (%s,%s,%s,'input','ready',%s,%s,%s,"
        "'text/plain','utf-8',%s,%s,now(),now())",
        (file_id, account, conversation, name, name, published.storage_key,
         len(body), published.sha256),
    )
    return file_id


def _conversation(commands, account):
    return UUID(commands.create_conversation(account, str(uuid4()))["conversation_id"])


def _run(commands, account, conversation):
    return UUID(commands.send_message(account, conversation, str(uuid4()), "read")['run_id'])


def test_cross_conversation_candidate_and_file_id_are_run_scoped(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    info = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    a, b, c = (_conversation(commands, account_id) for _ in range(3))
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    file_id = _file(db, store, account_id, a, "paper.txt", b"A's paper")
    entry = catalog.save_file(account_id, info, file_id, "paper.txt", "save:paper")
    policy.grant(account_id, "conversation", b, info,
                 ["list_metadata", "read_content"], True)
    with pytest.raises(ResourceDenied):
        commands.send_message(account_id, c, str(uuid4()), "inject", file_ids=(file_id,))
    run_b = _run(commands, account_id, b)
    run_c = _run(commands, account_id, c)
    assert policy.candidates(account_id, run_b)["count"] == 1
    assert policy.candidates(account_id, run_c)["count"] == 0
    with pytest.raises(ResourceDenied):
        policy.select(account_id, run_c, entry)
    assert db.execute(
        "SELECT reason FROM run_resource_denials WHERE run_id=%s AND operation='select'",
        (run_c,),
    ).fetchone()[0] == "resource is not a frozen Run candidate"
    scope = RunFileWorkspace(worker_database_url, store, tmp_path / "execution")
    with scope.prepare(account_id, run_b) as prepared:
        assert prepared.inputs == ()
        result = prepared.select(entry)
        assert result["file_id"] == str(file_id)
        assert policy.candidates(account_id, run_b)["candidates"][0]["read"] is False
        resource = FileResourceResolver(prepared).resolve(result["logical_name"])
        assert policy.candidates(account_id, run_b)["candidates"][0]["read"] is True
        assert resource.local_path.read_bytes() == b"A's paper"
    with scope.prepare(account_id, run_c) as prepared:
        with pytest.raises(LookupError):
            FileResourceResolver(prepared).resolve(str(file_id))


def test_candidate_freeze_version_and_revocation_union(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url, commands)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    info = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    a, b = (_conversation(commands, account_id) for _ in range(2))
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    v1 = _file(db, store, account_id, a, "v1.txt", b"version one")
    v2 = _file(db, store, account_id, a, "v2.txt", b"version two")
    v3 = _file(db, store, account_id, a, "v3.txt", b"version three")
    destination = uuid4()
    sha = db.execute("SELECT sha256 FROM stored_files WHERE file_id=%s", (v1,)).fetchone()[0]
    with db.transaction():
        db.execute(
            "INSERT INTO persistent_file_destinations(destination_id,account_id,current_revision,"
            "current_file_id,current_sha256) VALUES (%s,%s,1,%s,%s)",
            (destination, account_id, v1, sha),
        )
        db.execute(
            "INSERT INTO persistent_file_revisions(account_id,destination_id,revision,"
            "file_id,sha256,operation_id) VALUES (%s,%s,1,%s,%s,'v1')",
            (account_id, destination, v1, sha),
        )
    entry = uuid4()
    db.execute(
        "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,kind,name,"
        "name_key,destination_id) VALUES (%s,%s,%s,%s,'file','version.txt','version.txt',%s)",
        (entry, account_id, UUID(tree["workspace_id"]), info, destination),
    )
    grants = policy.grant(account_id, "conversation", b, info,
                          ["list_metadata", "read_content"], True)
    direct = policy.grant(account_id, "conversation", b, entry,
                          ["read_content"], False)[0]
    c = _conversation(commands, account_id)
    policy.grant(account_id, "conversation", c, entry,
                 ["list_metadata", "read_content"], False)
    attached = UUID(commands.send_message(
        account_id, c, str(uuid4()), "attached", file_ids=(v1,)
    )["run_id"])
    run_id = _run(commands, account_id, b)
    assert policy.candidates(account_id, run_id)["count"] == 1
    sha2 = db.execute("SELECT sha256 FROM stored_files WHERE file_id=%s", (v2,)).fetchone()[0]
    with db.transaction():
        db.execute("INSERT INTO persistent_file_revisions(account_id,destination_id,"
                   "revision,file_id,sha256,operation_id) VALUES (%s,%s,2,%s,%s,'v2')",
                   (account_id, destination, v2, sha2))
        db.execute("UPDATE persistent_file_destinations SET current_file_id=%s,"
                   "current_revision=2,current_sha256=%s WHERE destination_id=%s",
                   (v2, sha2, destination))
    assert policy.select(account_id, run_id, entry)["file_id"] == str(v2)
    assert db.execute("SELECT file_id FROM run_files WHERE run_id=%s AND direction='input'",
                      (attached,)).fetchone()[0] == v1
    sha3 = db.execute("SELECT sha256 FROM stored_files WHERE file_id=%s", (v3,)).fetchone()[0]
    with db.transaction():
        db.execute("INSERT INTO persistent_file_revisions(account_id,destination_id,"
                   "revision,file_id,sha256,operation_id) VALUES (%s,%s,3,%s,%s,'v3')",
                   (account_id, destination, v3, sha3))
        db.execute("UPDATE persistent_file_destinations SET current_file_id=%s,"
                   "current_revision=3,current_sha256=%s WHERE destination_id=%s",
                   (v3, sha3, destination))
    assert policy.select(account_id, run_id, entry)["file_id"] == str(v2)
    row = db.execute("SELECT fixed_revision FROM run_resource_candidates WHERE run_id=%s",
                     (run_id,)).fetchone()
    assert row[0] == 2
    policy.revoke(account_id, UUID(direct), commands)
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == "queued"
    scope = RunFileWorkspace(worker_database_url, store, tmp_path / "execution")
    with scope.prepare(account_id, run_id) as prepared:
        chosen = prepared.select(entry)
        assert FileResourceResolver(prepared).resolve(chosen["logical_name"]).local_path.read_bytes() == b"version two"
        policy.revoke(account_id, UUID(grants[1]), commands)
        with pytest.raises(ResourceDenied):
            FileResourceResolver(prepared).resolve(chosen["logical_name"])
    assert db.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()[0] == "cancelled"
    with pytest.raises(ResourceDenied):
        policy.select(account_id, run_id, entry)


def test_snapshot_does_not_expand_and_limit_is_explicit(
    db, account_id, database_url, tmp_path,
):
    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    info = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    a, b = (_conversation(commands, account_id) for _ in range(2))
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    file_id = _file(db, store, account_id, a, "base.txt", b"base")
    policy.grant(account_id, "conversation", b, info,
                 ["list_metadata", "read_content"], True)
    old = _run(commands, account_id, b)
    assert policy.candidates(account_id, old)["count"] == 0
    entry = catalog.save_file(account_id, info, file_id, "later.txt", "save:later")
    assert policy.candidates(account_id, old)["count"] == 0
    with pytest.raises(ResourceDenied):
        policy.select(account_id, old, entry)
    commands.cancel_run(account_id, old, str(uuid4()))
    new = _run(commands, account_id, b)
    assert policy.candidates(account_id, new)["count"] == 1
    first = policy.candidates(account_id, new, limit=1)
    assert first["candidates"][0]["node_id"] == str(entry)
    commands.cancel_run(account_id, new, str(uuid4()))
    for number in range(500):
        node = uuid4()
        name = f"extra-{number:03}.txt"
        db.execute(
            "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,kind,"
            "name,name_key,file_id) VALUES (%s,%s,%s,%s,'file',%s,%s,%s)",
            (node, account_id, UUID(tree["workspace_id"]), info, name, name, file_id),
        )
    with pytest.raises(SnapshotLimitExceeded):
        _run(commands, account_id, b)
    assert db.execute("SELECT count(*) FROM runs WHERE conversation_id=%s", (b,)).fetchone()[0] == 2


def test_explicit_attachment_revocation_blocks_read_and_failed_retry(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    commands = CommandService(database_url)
    policy = ResourcePolicy(database_url)
    a = _conversation(commands, account_id)
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    file_id = _file(db, store, account_id, a, "private.txt", b"private")
    failed = UUID(commands.send_message(
        account_id, a, str(uuid4()), "first", file_ids=(file_id,)
    )["run_id"])
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, failed)
    worker.fail_run(account_id, failed, "model_error")
    run_id = UUID(commands.send_message(
        account_id, a, str(uuid4()), "read", file_ids=(file_id,)
    )["run_id"])
    scope = RunFileWorkspace(worker_database_url, store, tmp_path / "execution")
    with scope.prepare(account_id, run_id) as prepared:
        assert FileResourceResolver(prepared).resolve("private.txt").local_path.read_bytes() == b"private"
        affected = policy.revoke_conversation_file(account_id, a, file_id, commands)
        assert affected == [run_id]
        with pytest.raises(ResourceDenied):
            FileResourceResolver(prepared).resolve("private.txt")
    with pytest.raises(ResourceDenied):
        commands.send_message(account_id, a, str(uuid4()), "reselect", file_ids=(file_id,))
    with pytest.raises(ResourceDenied):
        commands.retry_run(account_id, failed, str(uuid4()))
    with pytest.raises(RunFileScopeUnavailable):
        with scope.prepare(account_id, run_id):
            pass


def test_run_output_commit_rechecks_create_permission(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    from file_runtime import OutputPublisher
    from workspace.file_scope import RunFileScope

    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url, commands)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    results = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    conversation = _conversation(commands, account_id)
    grant = policy.grant(account_id, "conversation", conversation, results,
                         ["create_child"], False)[0]
    run_id = _run(commands, account_id, conversation)
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "report.md").write_bytes(b"# report")
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    published = OutputPublisher(worker_database_url, store).publish(
        RunFileScope(run_id, tmp_path / "inputs", tmp_path / "scratch", outputs, ()),
        "report:1", "report.md",
    )
    policy.revoke(account_id, UUID(grant), commands)
    with pytest.raises(ResourceDenied):
        catalog.save_file(account_id, results, published.file_id, "report.md", "agent-save:1",
                          agent_run_id=run_id)
    assert db.execute("SELECT count(*) FROM workspace_nodes WHERE file_id=%s",
                      (published.file_id,)).fetchone()[0] == 0


def test_move_preview_rejects_changed_topology_or_policy(
    db, account_id, database_url,
):
    from workspace.catalog import WorkspaceConflict

    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url, commands)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    root = UUID(tree["root_id"])
    info = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    folder = catalog.create_directory(account_id, info, "folder")
    preview = catalog.preview_change(account_id, folder)
    conversation = _conversation(commands, account_id)
    policy.grant(account_id, "conversation", conversation, info,
                 ["list_metadata"], True)
    with pytest.raises(WorkspaceConflict):
        catalog.move(account_id, folder, root, "folder", preview["preview_token"])
    fresh = catalog.preview_change(account_id, folder)
    catalog.move(account_id, folder, root, "folder", fresh["preview_token"])
    with pytest.raises(WorkspaceConflict):
        catalog.remove(account_id, folder, fresh["preview_token"])


@pytest.mark.asyncio
async def test_active_read_tool_waits_for_adapter_exit_and_rejects_reentry(
    db, account_id, database_url, worker_database_url, tmp_path,
):
    from file_domain.models import SourceLocator, TextView
    from file_runtime import FileAdapterRegistry
    from sandbox.tools.local.file_read import create_file_read_tools

    commands = CommandService(database_url)
    catalog = WorkspaceCatalog(database_url, commands)
    policy = ResourcePolicy(database_url)
    tree = catalog.initialize(account_id)
    info = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料"))
    source, reader = (_conversation(commands, account_id) for _ in range(2))
    store = TenantFileStore(tmp_path / "store", max_bytes=1024)
    file_id = _file(db, store, account_id, source, "active.txt", b"active content")
    entry = catalog.save_file(account_id, info, file_id, "active.txt", "save:active")
    grants = policy.grant(account_id, "conversation", reader, info,
                          ["list_metadata", "read_content"], True)
    run_id = _run(commands, account_id, reader)
    entered, release, exited = threading.Event(), threading.Event(), threading.Event()

    class PausedAdapter:
        def convert(self, resource, *, max_chars):
            entered.set()
            try:
                assert release.wait(5)
                return TextView("active content", SourceLocator(resource.file_id,
                                resource.logical_name), False, {})
            finally:
                exited.set()

    registry = FileAdapterRegistry()
    registry.register("fast_text", PausedAdapter(), extensions=("txt",))
    scope = RunFileWorkspace(worker_database_url, store, tmp_path / "execution")
    with scope.prepare(account_id, run_id) as prepared:
        prepared.select(entry)
        read = {tool.name: tool for tool in create_file_read_tools(
            lambda: prepared, registry)}["read_file"]
        running = asyncio.create_task(read.ainvoke({"file": "active.txt"}))
        assert await asyncio.to_thread(entered.wait, 5)
        policy.revoke(account_id, UUID(grants[1]), commands)
        running.cancel()
        await asyncio.sleep(0)
        assert not running.done() and not exited.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert exited.is_set()
        with pytest.raises(ResourceDenied):
            await read.ainvoke({"file": "active.txt"})
