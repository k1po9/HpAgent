from __future__ import annotations

from uuid import UUID, uuid4

from conversation_domain.commands import CommandService
from file_runtime import OutputPublisher
from storage.tenant_file_store import TenantFileStore
from workspace.file_scope import RunFileScope
from workspace.resources import ResourcePolicy


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    result = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        result["Idempotency-Key"] = key
    return result


def test_workspace_version_api_and_conflict_output(
    tmp_path, seed_identity, client_factory, database_url, worker_database_url,
):
    account_id = seed_identity("workspace-p3-user")
    store_root = tmp_path / "store"
    client = client_factory(file_upload_enabled=True, file_store_root=str(store_root))
    assert client.post("/auth/login", json={
        "username": "workspace-p3-user", "password": "correct-password",
    }, follow_redirects=False).status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    tree = client.get("/api/v1/workspace").json()
    parent = next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料")
    source = client.post("/api/v1/conversations", json={"title": "A"},
                         headers=_headers(csrf, str(uuid4()))).json()["conversation"]["conversation_id"]
    upload = client.post(f"/api/v1/conversations/{source}/uploads", json={
        "file_name": "base.txt", "size_bytes": 2, "content_type": "text/plain",
    }, headers=_headers(csrf, str(uuid4())))
    base = upload.json()["file"]["file_id"]
    assert client.put(f"/api/v1/uploads/{base}/content", content=b"v1", headers={
        **_headers(csrf), "Content-Type": "application/octet-stream",
    }).status_code == 200
    entry = client.post("/api/v1/workspace/files", json={
        "parent_id": parent, "file_id": base, "name": "base.txt",
    }, headers=_headers(csrf, str(uuid4()))).json()["node_id"]
    upgraded = client.post(f"/api/v1/workspace/nodes/{entry}/upgrade",
                           json={}, headers=_headers(csrf))
    assert upgraded.status_code == 200
    assert client.post(f"/api/v1/workspace/nodes/{entry}/upgrade",
                       json={}, headers=_headers(csrf)).json() == upgraded.json()
    assert client.get(f"/api/v1/workspace/nodes/{entry}/versions").json()[
        "current"]["revision"] == 1
    conversation = client.post("/api/v1/conversations", json={"title": "C"},
                               headers=_headers(csrf, str(uuid4()))).json()["conversation"]["conversation_id"]
    grant = client.post(f"/api/v1/conversations/{conversation}/resources", json={
        "node_id": entry,
        "operations": ["list_metadata", "read_content", "update_content"],
    }, headers=_headers(csrf))
    assert grant.status_code == 201
    sent = client.post(f"/api/v1/conversations/{conversation}/messages", json={
        "content": "edit"}, headers=_headers(csrf, str(uuid4())))
    run_id = sent.json()["run"]["run_id"]
    ResourcePolicy(database_url).select(account_id, UUID(run_id), UUID(entry))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    (outputs / "result.txt").write_bytes(b"v2")
    published = OutputPublisher(worker_database_url, TenantFileStore(
        store_root, max_bytes=1024)).publish(
            RunFileScope(UUID(run_id), tmp_path / "inputs", tmp_path / "scratch", outputs, ()),
            "p3:output", "result.txt")
    listed = client.get(f"/api/v1/runs/{run_id}/published-files")
    assert listed.status_code == 200
    assert listed.json()["files"][0]["file_id"] == str(published.file_id)
    commands = CommandService(database_url)
    assert commands.start_run(account_id, UUID(run_id))
    assert commands.complete_run(account_id, UUID(run_id), "edited")
    body = {"run_id": run_id, "file_id": str(published.file_id),
            "expected_revision": 1, "expected_sha256": upgraded.json()["sha256"]}
    operation = str(uuid4())
    committed = client.post(f"/api/v1/workspace/nodes/{entry}/versions", json=body,
                            headers=_headers(csrf, operation))
    assert committed.status_code == 201
    assert committed.json()["revision"] == 2
    assert client.post(f"/api/v1/workspace/nodes/{entry}/versions", json=body,
                       headers=_headers(csrf, operation)).json() == committed.json()
    conflict = client.post(f"/api/v1/workspace/nodes/{entry}/versions", json=body,
                           headers=_headers(csrf, str(uuid4())))
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "workspace_version_conflict"
    assert conflict.json()["error"]["details"]["published_file_id"] == str(published.file_id)
    assert client.get(f"/api/v1/files/{base}/content").content == b"v1"
    assert client.get(f"/api/v1/files/{published.file_id}/content").content == b"v2"
