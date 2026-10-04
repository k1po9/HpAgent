from __future__ import annotations

from uuid import uuid4

import pytest

from storage.tenant_file_store import TenantFileStore
from web_domain.file_cleanup import FileCleanupService

pytestmark = pytest.mark.postgres


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    result = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        result["Idempotency-Key"] = key
    return result


def test_direct_upload_save_download_and_cross_conversation(
    tmp_path, seed_identity, client_factory, db, worker_database_url,
):
    account_id = seed_identity("workspace-direct-upload")
    client = client_factory(file_upload_enabled=True, file_store_root=str(tmp_path / "store"))
    assert client.post("/auth/login", json={
        "username": "workspace-direct-upload", "password": "correct-password",
    }, follow_redirects=False).status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    tree = client.get("/api/v1/workspace").json()
    parent = next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料")
    body = b"direct workspace upload"
    rejected = client.post("/api/v1/workspace/uploads", json={
        "file_name": "retry.txt", "size_bytes": len(body), "content_type": "text/plain",
        "sha256": "0" * 64,
    }, headers=_headers(csrf, str(uuid4())))
    assert rejected.status_code == 201
    assert client.put(rejected.json()["content_url"], content=body,
        headers={**_headers(csrf), "Content-Type": "application/octet-stream"}).status_code == 422
    assert db.execute("SELECT status FROM stored_files WHERE file_id=%s",
                      (rejected.json()["file"]["file_id"],)).fetchone()[0] == "rejected"
    key = str(uuid4())
    payload = {"file_name": "direct.txt", "size_bytes": len(body),
               "content_type": "text/plain"}
    created = client.post("/api/v1/workspace/uploads", json=payload,
                          headers=_headers(csrf, key))
    assert created.status_code == 201
    replay = client.post("/api/v1/workspace/uploads", json=payload,
                         headers=_headers(csrf, key))
    assert replay.status_code == 201
    assert replay.json() == created.json()
    file_id = created.json()["file"]["file_id"]
    assert client.put(created.json()["content_url"], content=body,
        headers={**_headers(csrf), "Content-Type": "application/octet-stream"}).status_code == 200
    row = db.execute("SELECT conversation_id,source_workspace_id FROM stored_files "
                     "WHERE file_id=%s", (file_id,)).fetchone()
    assert row[0] is None
    assert str(row[1]) == tree["workspace_id"]
    saved = client.post("/api/v1/workspace/files", json={
        "parent_id": parent, "file_id": file_id, "name": "direct.txt",
    }, headers=_headers(csrf, str(uuid4())))
    assert saved.status_code == 201
    assert client.get(f"/api/v1/files/{file_id}/content").content == body
    assert client.get("/api/v1/workspace/space").json()["physical_bytes"] == len(body)
    assert client.get("/api/v1/persistent-files/legacy/path").status_code == 404
    assert client.post("/api/v1/persistent-files/legacy/path", json={
        "logical_path": "legacy/path", "file_id": file_id,
    }, headers=_headers(csrf, str(uuid4()))).status_code == 404
    assert db.execute(
        "SELECT count(*) FROM information_schema.columns WHERE table_schema='hpagent' "
        "AND table_name='persistent_file_destinations' AND column_name='logical_path'"
    ).fetchone()[0] == 0
    preview = client.get(f"/api/v1/workspace/nodes/{saved.json()['node_id']}/impact")
    assert preview.status_code == 200
    moved = client.patch(f"/api/v1/workspace/nodes/{saved.json()['node_id']}", json={
        "parent_id": parent, "name": "moved.txt",
        "preview_token": preview.json()["preview_token"],
    }, headers=_headers(csrf))
    assert moved.status_code == 200
    assert client.get(f"/api/v1/files/{file_id}/content").content == body

    conversation = client.post("/api/v1/conversations", json={"title": "Reader"},
                               headers=_headers(csrf, str(uuid4()))).json()["conversation"]["conversation_id"]
    granted = client.post(f"/api/v1/conversations/{conversation}/resources", json={
        "node_id": parent, "operations": ["list_metadata", "read_content"],
        "recursive": True,
    }, headers=_headers(csrf))
    assert granted.status_code == 201
    # Retrying authorization returns the same rule identities, not duplicate grants.
    again = client.post(f"/api/v1/conversations/{conversation}/resources", json={
        "node_id": parent, "operations": ["list_metadata", "read_content"], "recursive": True,
    }, headers=_headers(csrf))
    assert again.json()["grant_ids"] == granted.json()["grant_ids"]
    assert len(client.get(f"/api/v1/conversations/{conversation}/resources").json()["grants"]) == 2
    sent = client.post(f"/api/v1/conversations/{conversation}/messages", json={
        "content": "read direct upload",
    }, headers=_headers(csrf, str(uuid4())))
    assert sent.status_code == 202
    candidates = client.get(f"/api/v1/runs/{sent.json()['run']['run_id']}/resources")
    assert candidates.status_code == 200
    assert saved.json()["node_id"] in {item["node_id"] for item in candidates.json()["candidates"]}

    from uuid import UUID

    from application.context_assembly import ContextAssemblyService
    from application.context_builder import HarnessContextBuilder
    context = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    base = context.load_base(account_id, UUID(sent.json()["run"]["run_id"]))
    assert base.workspace_candidate_count == 1
    prompt = context.compose(base, ())[0]["content"]
    assert "moved.txt" in prompt and "select_run_candidate" in prompt
    other = client.post("/api/v1/conversations", json={"title": "Isolated"}, headers=_headers(csrf, str(uuid4()))).json()["conversation"]["conversation_id"]
    other_sent = client.post(f"/api/v1/conversations/{other}/messages", json={"content": "can you see my files"}, headers=_headers(csrf, str(uuid4())))
    isolated = context.load_base(account_id, UUID(other_sent.json()["run"]["run_id"]))
    assert isolated.workspace_candidate_count == 0
    assert "moved.txt" not in context.compose(isolated, ())[0]["content"]


def test_direct_upload_removed_entry_is_collected(
    tmp_path, seed_identity, client_factory, db, worker_database_url,
):
    seed_identity("workspace-direct-gc")
    store_root = tmp_path / "store"
    client = client_factory(file_upload_enabled=True, file_store_root=str(store_root))
    assert client.post("/auth/login", json={
        "username": "workspace-direct-gc", "password": "correct-password",
    }, follow_redirects=False).status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    content = b"collect me"
    upload = client.post("/api/v1/workspace/uploads", json={
        "file_name": "gc.txt", "size_bytes": len(content), "content_type": "text/plain",
    }, headers=_headers(csrf, str(uuid4())))
    assert upload.status_code == 201
    file_id = upload.json()["file"]["file_id"]
    root = client.get("/api/v1/workspace").json()["root_id"]
    assert client.put(upload.json()["content_url"], content=content,
        headers={**_headers(csrf), "Content-Type": "application/octet-stream"}).status_code == 200
    saved = client.post("/api/v1/workspace/files", json={
        "parent_id": root, "file_id": file_id, "name": "gc.txt",
    }, headers=_headers(csrf, str(uuid4())))
    assert saved.status_code == 201
    assert client.delete(f"/api/v1/files/{file_id}", headers=_headers(csrf)).status_code == 409
    node = saved.json()["node_id"]
    preview = client.get(f"/api/v1/workspace/nodes/{node}/impact").json()
    assert client.delete(f"/api/v1/workspace/nodes/{node}", headers={
        **_headers(csrf), "X-Workspace-Preview": preview["preview_token"],
    }).status_code == 204
    assert client.delete(f"/api/v1/files/{file_id}", headers=_headers(csrf)).status_code == 204
    store = TenantFileStore(store_root, max_bytes=1024 * 1024)
    assert FileCleanupService(worker_database_url, store).cleanup_once().deleted == 1
    assert db.execute("SELECT storage_key FROM stored_files WHERE file_id=%s",
                      (file_id,)).fetchone()[0] is None


def test_directory_conflicts_have_actionable_reasons(seed_identity, client_factory):
    seed_identity("directory-reasons")
    client = client_factory(file_upload_enabled=True)
    client.post("/auth/login", json={"username": "directory-reasons", "password": "correct-password"}, follow_redirects=False)
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    tree = client.get("/api/v1/workspace").json()
    parent = next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料")
    payload = {"parent_id": parent, "name": "项目"}
    result = client.post("/api/v1/workspace/directories", json=payload, headers=_headers(csrf))
    assert result.status_code == 201
    child = next(n for n in client.get("/api/v1/workspace").json()["nodes"] if n["node_id"] == result.json()["node_id"])
    assert child["parent_id"] == parent
    duplicate = client.post("/api/v1/workspace/directories", json=payload, headers=_headers(csrf))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["details"]["reason"] == "name_exists"
    assert duplicate.json()["error"]["request_id"]
    assert client.post("/api/v1/workspace/directories", json={**payload, "parent_id": tree["root_id"]}, headers=_headers(csrf)).status_code == 201
    invalid = client.post("/api/v1/workspace/directories", json={**payload, "name": "../invalid"}, headers=_headers(csrf))
    assert invalid.status_code == 409
    assert invalid.json()["error"]["details"]["reason"] == "invalid_name"
