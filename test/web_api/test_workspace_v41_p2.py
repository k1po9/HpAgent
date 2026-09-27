from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from workspace.resources import ResourcePolicy

pytestmark = pytest.mark.postgres


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def test_task_grant_revoke_checks_owner_task_and_replay(
    tmp_path, seed_identity, client_factory,
):
    seed_identity("task-resource-owner")
    seed_identity("task-resource-other")
    owner = client_factory(file_upload_enabled=True, file_store_root=str(tmp_path / "store"))
    other = client_factory(file_upload_enabled=True, file_store_root=str(tmp_path / "store"))
    for client, username in ((owner, "task-resource-owner"), (other, "task-resource-other")):
        assert client.post("/auth/login", json={"username": username,
            "password": "correct-password"}, follow_redirects=False).status_code == 303
    csrf = owner.get("/api/v1/me").json()["csrf_token"]
    other_csrf = other.get("/api/v1/me").json()["csrf_token"]
    tree = owner.get("/api/v1/workspace").json()
    info = next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料")
    task = owner.post("/api/v1/tasks", json={"title": "Grant test", "objective": "Read",
        "output_required": False}, headers=_headers(csrf, str(uuid4())))
    assert task.status_code == 201, task.text
    task_id = task.json()["task"]["task_id"]
    second = owner.post("/api/v1/tasks", json={"title": "Second", "objective": "Read",
        "output_required": False}, headers=_headers(csrf, str(uuid4())))
    assert second.status_code == 201, second.text
    other_task_id = second.json()["task"]["task_id"]
    grant = owner.post(f"/api/v1/tasks/{task_id}/resources", json={
        "node_id": info, "operations": ["list_metadata", "read_content"],
        "recursive": True,
    }, headers=_headers(csrf))
    assert grant.status_code == 201, grant.text
    grant_id = grant.json()["grant_ids"][1]
    path = f"/api/v1/tasks/{task_id}/resources/{grant_id}"
    assert other.delete(path, headers=_headers(other_csrf)).status_code == 404
    assert owner.delete(f"/api/v1/tasks/{other_task_id}/resources/{grant_id}",
                        headers=_headers(csrf)).status_code == 404
    assert owner.delete(path, headers=_headers(csrf)).json() == {"affected_runs": []}
    assert owner.delete(path, headers=_headers(csrf)).json() == {"affected_runs": []}


def test_conversation_grant_snapshot_revoke_and_owner_download(
    tmp_path, seed_identity, client_factory, db, database_url,
):
    account_id = seed_identity("workspace-p2-user")
    client = client_factory(file_upload_enabled=True, file_store_root=str(tmp_path / "store"))
    assert client.post("/auth/login", json={
        "username": "workspace-p2-user", "password": "correct-password",
    }, follow_redirects=False).status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    tree = client.get("/api/v1/workspace").json()
    info = next(n["node_id"] for n in tree["nodes"] if n["name"] == "资料")

    def conversation(name: str) -> str:
        response = client.post("/api/v1/conversations", json={"title": name},
                               headers=_headers(csrf, str(uuid4())))
        assert response.status_code == 201
        return response.json()["conversation"]["conversation_id"]

    a, b, c = (conversation(name) for name in ("A", "B", "C"))
    payload = b"%PDF-1.4\nCross conversation fixture\n%%EOF\n"
    upload = client.post(f"/api/v1/conversations/{a}/uploads", json={
        "file_name": "shared.pdf", "size_bytes": len(payload), "content_type": "application/pdf",
    }, headers=_headers(csrf, str(uuid4())))
    assert upload.status_code == 201
    file_id = upload.json()["file"]["file_id"]
    assert client.put(f"/api/v1/uploads/{file_id}/content", content=payload,
        headers={**_headers(csrf), "Content-Type": "application/octet-stream"}).status_code == 200
    saved = client.post("/api/v1/workspace/files", json={
        "parent_id": info, "file_id": file_id, "name": "shared.pdf",
    }, headers=_headers(csrf, str(uuid4())))
    assert saved.status_code == 201
    entry = saved.json()["node_id"]
    granted = client.post(f"/api/v1/conversations/{b}/resources", json={
        "node_id": info, "operations": ["list_metadata", "read_content"],
        "recursive": True,
    }, headers=_headers(csrf))
    assert granted.status_code == 201
    assert len(client.get(f"/api/v1/conversations/{b}/resources").json()["grants"]) == 2
    denied = client.post(f"/api/v1/conversations/{c}/messages", json={
        "content": "guess", "file_ids": [file_id],
    }, headers=_headers(csrf, str(uuid4())))
    assert denied.status_code == 403
    sent = client.post(f"/api/v1/conversations/{b}/messages", json={"content": "read"},
                       headers=_headers(csrf, str(uuid4())))
    assert sent.status_code == 202
    run_id = sent.json()["run"]["run_id"]
    resources = client.get(f"/api/v1/runs/{run_id}/resources")
    assert resources.status_code == 200
    assert resources.json()["candidates"][0]["node_id"] == entry
    assert resources.json()["candidates"][0]["fixed"] is False
    ResourcePolicy(database_url).select(account_id, UUID(run_id), UUID(entry))
    read_grant = granted.json()["grant_ids"][1]
    revoked = client.delete(f"/api/v1/conversations/{b}/resources/{read_grant}",
                            headers=_headers(csrf))
    assert revoked.status_code == 200
    assert revoked.json()["affected_runs"] == [{"run_id": run_id, "stop_state": "stopped"}]
    repeated = client.delete(f"/api/v1/conversations/{b}/resources/{read_grant}",
                             headers=_headers(csrf))
    assert repeated.status_code == 200
    assert repeated.json()["affected_runs"] == []
    assert client.get(f"/api/v1/files/{file_id}/content").content == payload
    assert client.get(f"/api/v1/runs/{run_id}").json()["run"]["status"] == "cancelled"
