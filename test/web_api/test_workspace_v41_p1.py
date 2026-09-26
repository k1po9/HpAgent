from __future__ import annotations

from uuid import uuid4


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def test_upload_save_move_download_via_workspace_api(
    tmp_path, seed_identity, client_factory, db,
):
    seed_identity("workspace-p1-user")
    client = client_factory(file_upload_enabled=True, file_store_root=str(tmp_path / "store"))
    assert client.post("/auth/login", json={
        "username": "workspace-p1-user", "password": "correct-password",
    }, follow_redirects=False).status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    initial = client.get("/api/v1/workspace")
    assert initial.status_code == 200
    tree = initial.json()
    root = tree["root_id"]
    assert len(tree["nodes"]) == 4
    assert client.get("/api/v1/workspace").json() == tree
    created = client.post("/api/v1/workspace/directories",
        json={"parent_id": root, "name": "项目"}, headers=_headers(csrf))
    assert created.status_code == 201
    project = created.json()["node_id"]
    conversation = client.post("/api/v1/conversations", json={"title": "P1"},
        headers=_headers(csrf, str(uuid4()))).json()["conversation"]["conversation_id"]
    content = b"# workspace report\n"
    upload = client.post(f"/api/v1/conversations/{conversation}/uploads",
        json={"file_name": "report.md", "size_bytes": len(content),
              "content_type": "text/markdown"}, headers=_headers(csrf, str(uuid4())))
    assert upload.status_code == 201
    file_id = upload.json()["file"]["file_id"]
    ready = client.put(f"/api/v1/uploads/{file_id}/content", content=content,
        headers={**_headers(csrf), "Content-Type": "application/octet-stream"})
    assert ready.status_code == 200
    before = db.execute("SELECT count(*) FROM stored_files").fetchone()[0]
    save_key = str(uuid4())
    saved = client.post("/api/v1/workspace/files",
        json={"parent_id": project, "file_id": file_id, "name": "report.md"},
        headers=_headers(csrf, save_key))
    assert saved.status_code == 201
    entry_id = saved.json()["node_id"]
    assert client.post("/api/v1/workspace/files",
        json={"parent_id": project, "file_id": file_id, "name": "report.md"},
        headers=_headers(csrf, save_key)).json()["node_id"] == entry_id
    found = client.get("/api/v1/workspace/search", params={"name": "report",
        "content_type": "text/markdown"})
    assert found.status_code == 200
    assert found.json()["items"][0]["node_id"] == entry_id
    assert client.get("/api/v1/workspace/space").json()["bytes_with_active_entry"] == len(content)
    retained = client.get(f"/api/v1/workspace/files/{file_id}/retention")
    assert retained.status_code == 200
    assert retained.json()["references"]["active_entries"] == 1
    trace = client.get(f"/api/v1/workspace/nodes/{entry_id}/trace")
    assert trace.status_code == 200
    assert trace.json()["saves"][0]["operation_id"] == save_key
    preview = client.get(f"/api/v1/workspace/nodes/{entry_id}/impact").json()
    moved = client.patch(f"/api/v1/workspace/nodes/{entry_id}",
        json={"parent_id": root, "name": "renamed.md",
              "preview_token": preview["preview_token"]}, headers=_headers(csrf))
    assert moved.status_code == 200
    assert db.execute("SELECT count(*) FROM stored_files").fetchone()[0] == before
    updated = client.get("/api/v1/workspace").json()
    node = next(item for item in updated["nodes"] if item["node_id"] == entry_id)
    assert node["name"] == "renamed.md" and node["file_id"] == file_id
    assert node["source"]["purpose"] == "input"
    assert client.get(f"/api/v1/files/{file_id}/content").content == content
    preview = client.get(f"/api/v1/workspace/nodes/{entry_id}/impact").json()
    assert client.delete(f"/api/v1/workspace/nodes/{entry_id}", headers={
        **_headers(csrf), "X-Workspace-Preview": preview["preview_token"],
    }).status_code == 204
    assert all(item["node_id"] != entry_id for item in client.get("/api/v1/workspace").json()["nodes"])
