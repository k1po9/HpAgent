"""Real PostgreSQL/FileStore Workspace discovery and lazy materialization probe."""
from __future__ import annotations

import json
import os
import resource
import time
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

from conversation_domain.commands import CommandService
from file_runtime import FileResourceResolver
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog
from workspace.discovery import WorkspaceDiscovery
from workspace.file_scope import RunFileWorkspace
from workspace.resources import ResourcePolicy, SnapshotLimitExceeded


def rss_kib() -> int:
    return int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)


def main() -> None:
    migration_url = os.environ["MIGRATION_DATABASE_URL"]
    api_url = os.environ["APP_DATABASE_URL"]
    worker_url = os.environ["WORKER_DATABASE_URL"]
    store = TenantFileStore(Path(os.environ["FILE_STORE_ROOT"]), max_bytes=1024)
    commands = CommandService(api_url)
    catalog = WorkspaceCatalog(api_url)
    policy = ResourcePolicy(api_url)
    discovery = WorkspaceDiscovery(api_url)
    account_id = uuid4()
    with psycopg.connect(migration_url, autocommit=True) as db:
        db.execute("SET search_path TO hpagent,public")
        db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account_id,))
    tree = catalog.initialize(account_id)
    workspace_id = UUID(tree["workspace_id"])
    root = UUID(tree["root_id"])
    folders = [catalog.create_directory(account_id, root, f"batch-{i:03d}")
               for i in range(100)]
    entries: list[list[UUID]] = [[] for _ in folders]
    began = time.perf_counter()
    baseline_rss = rss_kib()
    for i in range(10_000):
        file_id = uuid4()
        body = f"distinct-file-{i:05d}".encode().ljust(128, b".")
        staged = store.stage(file_id, [body], declared_size=len(body))
        published = store.publish(account_id, file_id, staged)
        folder = i // 100
        node_id = uuid4()
        name = f"file-{i:05d}.txt"
        with psycopg.connect(migration_url, autocommit=True) as db:
            db.execute("SET search_path TO hpagent,public")
            db.execute(
                "INSERT INTO stored_files(file_id,account_id,source_workspace_id,purpose,status,"
                "original_name,display_name,storage_key,content_type,encoding,size_bytes,sha256,ready_at) "
                "VALUES (%s,%s,%s,'input','ready',%s,%s,%s,'text/plain','utf-8',%s,%s,now())",
                (file_id, account_id, workspace_id, name, name, published.storage_key,
                 len(body), published.sha256),
            )
            db.execute(
                "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,kind,"
                "name,name_key,file_id) VALUES (%s,%s,%s,%s,'file',%s,%s,%s)",
                (node_id, account_id, workspace_id, folders[folder], name, name, file_id),
            )
        entries[folder].append(node_id)
        if i + 1 in {1_000, 10_000}:
            folder_ids = folders[:4] if i + 1 == 1_000 else folders[-4:]
            selected_groups = entries[:4] if i + 1 == 1_000 else entries[-4:]
            conversation = UUID(commands.create_conversation(
                account_id, str(uuid4()))["conversation_id"])
            for folder_id in folder_ids:
                policy.grant(account_id, "conversation", conversation, folder_id,
                             ["list_metadata", "read_content"], True)
            run_id = UUID(commands.send_message(
                account_id, conversation, str(uuid4()), "scale probe")["run_id"])
            started = time.perf_counter()
            result = discovery.search(account_id, name="file-")
            candidates = policy.candidates(account_id, run_id, limit=100)
            found_at = time.perf_counter()
            selected = [node for group in selected_groups for node in group[:5]]
            materialized_bytes = 0
            scope = RunFileWorkspace(worker_url, store, Path(os.environ["RUN_SCOPE_ROOT"]))
            with scope.prepare(account_id, run_id) as prepared:
                for node_id in selected:
                    fixed = prepared.select(node_id)
                    resource_file = FileResourceResolver(prepared).resolve(fixed["logical_name"])
                    materialized_bytes += resource_file.local_path.stat().st_size
            done_at = time.perf_counter()
            print(json.dumps({
                "workspace_entries": i + 1,
                "distinct_files": i + 1,
                "total_file_bytes": (i + 1) * 128,
                "owner_search_page": len(result["items"]),
                "run_candidate_count": candidates["count"],
                "materialized_files": len(selected),
                "materialized_bytes": materialized_bytes,
                "discovery_seconds": round(found_at - started, 3),
                "select_materialize_seconds": round(done_at - found_at, 3),
                "population_seconds": round(done_at - began, 3),
                "peak_rss_kib": rss_kib(),
                "peak_rss_growth_kib": rss_kib() - baseline_rss,
            }), flush=True)
    over_limit = UUID(commands.create_conversation(
        account_id, str(uuid4()))["conversation_id"])
    for folder_id in folders[:6]:
        policy.grant(account_id, "conversation", over_limit, folder_id,
                     ["list_metadata"], True)
    try:
        commands.send_message(account_id, over_limit, str(uuid4()), "over limit")
    except SnapshotLimitExceeded:
        print(json.dumps({"candidate_over_limit": "explicitly_rejected"}), flush=True)
    else:
        raise AssertionError("more than 500 candidates were accepted")


if __name__ == "__main__":
    main()
