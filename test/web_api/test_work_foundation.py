"""Phase 1 Work commands through the authenticated HTTP boundary."""

from __future__ import annotations

from uuid import uuid4

import pytest

pytestmark = pytest.mark.postgres


def headers(csrf: str, key: str, etag: str | None = None) -> dict[str, str]:
    value = {"Origin": "https://testserver", "X-CSRF-Token": csrf,
             "Idempotency-Key": key}
    if etag:
        value["If-Match"] = etag
    return value


def login(client, name: str) -> str:
    response = client.post("/auth/login", json={"username": name,
        "password": "correct-password"}, follow_redirects=False)
    assert response.status_code == 303
    return client.get("/api/v1/me").json()["csrf_token"]


def requirement(objective: str) -> dict:
    return {"objective": objective, "capability_key": "reminder",
            "spec": {"schema_version": 1, "content": "Remember this"},
            "acceptance_criteria": [{"id": "sent", "required": True,
                "evidence_types": ["operation_receipt"]}]}


def test_work_http_lifecycle_and_cross_account_visibility(seed_identity, client_factory, db):
    account_id = seed_identity("work-owner")
    seed_identity("work-other")
    client = client_factory()
    csrf = login(client, "work-owner")
    key = str(uuid4())
    payload = {"title": "Reminder", "requirement": requirement("First objective")}
    accepted = client.post("/api/v1/works", json=payload, headers=headers(csrf, key))
    assert accepted.status_code == 201, accepted.text
    replay = client.post("/api/v1/works", json=payload, headers=headers(csrf, key))
    assert replay.status_code == 201 and replay.headers["Idempotency-Replayed"] == "true"
    assert replay.json() == accepted.json()
    conflict = client.post("/api/v1/works", json={**payload, "title": "Changed"},
                           headers=headers(csrf, key))
    assert conflict.status_code == 409

    work_id = accepted.json()["work"]["work_id"]
    url = f"/api/v1/works/{work_id}"
    assert client.get(url).headers["ETag"] == accepted.headers["ETag"]
    conversations = [uuid4(), uuid4()]
    for conversation_id in conversations:
        db.execute("INSERT INTO conversations(account_id,conversation_id) VALUES (%s,%s)",
                   (account_id, conversation_id))
        linked = client.put(f"{url}/conversations/{conversation_id}", json={},
            headers=headers(csrf, str(uuid4()), client.get(url).headers["ETag"]))
        assert linked.status_code == 200, linked.text
    assert set(client.get(url).json()["work"]["conversation_ids"]) == set(map(str, conversations))
    assert any(item["work_id"] == work_id for item in client.get("/api/v1/works").json()["items"])

    current_etag = client.get(url).headers["ETag"]
    revised = client.post(f"{url}/revisions", json={"requirement": requirement("New objective")},
        headers=headers(csrf, str(uuid4()), current_etag))
    assert revised.status_code == 200, revised.text
    assert revised.json()["work"]["current_requirement_revision"] == 2
    stale = client.post(f"{url}/pause", json={}, headers=headers(csrf, str(uuid4()), current_etag))
    assert stale.status_code == 409 and stale.json()["error"]["details"]["current"]["row_version"] > 1
    paused = client.post(f"{url}/pause", json={},
        headers=headers(csrf, str(uuid4()), revised.headers["ETag"]))
    assert paused.status_code == 200 and paused.json()["work"]["status"] == "paused"
    resumed = client.post(f"{url}/resume", json={},
        headers=headers(csrf, str(uuid4()), paused.headers["ETag"]))
    assert resumed.status_code == 200 and resumed.json()["work"]["status"] == "active"
    stopped = client.post(f"{url}/stop", json={},
        headers=headers(csrf, str(uuid4()), resumed.headers["ETag"]))
    assert stopped.status_code == 200 and stopped.json()["work"]["status"] == "stopped"

    other = client_factory()
    login(other, "work-other")
    assert other.get(url).status_code == 404
    assert other.get(f"{url}/events").status_code == 404
    assert other.get(f"{url}/runs").status_code == 404
