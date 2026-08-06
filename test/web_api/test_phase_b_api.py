from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest

from web_domain.outbox import OutboxService
from web_domain.services import CommandService

pytestmark = pytest.mark.postgres


def login(client, username: str = "alice") -> str:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "correct-password", "return_to": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    return str(me.json()["csrf_token"])


def command_headers(csrf: str, key: str | None = None) -> dict[str, str]:
    headers = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def create_conversation(client, csrf: str, title: str | None = None):
    return client.post(
        "/api/v1/conversations",
        json={"title": title},
        headers=command_headers(csrf, str(uuid4())),
    )


def test_b01_health_unknown_fields_and_security_headers(client_factory):
    client = client_factory()
    response = client.get("/health/live", headers={"X-Request-ID": "safe-request"})
    assert response.status_code == 200
    assert response.headers["x-request-id"] == "safe-request"
    assert response.headers["x-content-type-options"] == "nosniff"
    invalid = client.post(
        "/auth/login",
        json={"username": "nobody", "password": "bad", "unexpected": True},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"
    unsupported = client.post(
        "/auth/login", content="not json", headers={"Content-Type": "text/plain"}
    )
    assert unsupported.status_code == 415
    malformed = client.post(
        "/auth/login", content="{", headers={"Content-Type": "application/json"}
    )
    assert malformed.status_code == 400
    assert malformed.json()["error"]["code"] == "malformed_request"


def test_api_001_login_me_logout_and_unknown_identity(seed_identity, client_factory):
    seed_identity("alice")
    client = client_factory()
    assert client.get("/api/v1/conversations").status_code == 401
    unknown = client.post(
        "/auth/login",
        json={"username": "unknown", "password": "correct-password"},
        follow_redirects=False,
    )
    assert unknown.status_code == 401
    login_response = client.post(
        "/auth/login",
        json={"username": "alice", "password": "correct-password", "return_to": "/"},
        follow_redirects=False,
    )
    cookie = login_response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=lax" in cookie
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["csrf_token"] == csrf
    cookie = me.request.headers["cookie"]
    assert "__Host-hpagent_session=" in cookie
    logout = client.post(
        "/api/v1/auth/logout", headers=command_headers(csrf)
    )
    assert logout.status_code == 204
    assert client.get("/api/v1/me").status_code == 401


def test_api_022_csrf_and_origin_fail_before_transaction(logged_client, db):
    client, csrf, _ = logged_client
    wrong_origin = client.post(
        "/api/v1/conversations",
        json={"title": None},
        headers={
            "Origin": "https://evil.example",
            "X-CSRF-Token": csrf,
            "Idempotency-Key": str(uuid4()),
        },
    )
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["error"]["code"] == "csrf_invalid"
    assert db.execute("SELECT count(*) FROM conversations").fetchone()[0] == 0


def test_api_002_cross_account_is_opaque_404(seed_identity, client_factory):
    seed_identity("alice")
    seed_identity("bob")
    alice, bob = client_factory(), client_factory()
    alice_csrf, _ = login(alice, "alice"), login(bob, "bob")
    created = create_conversation(alice, alice_csrf).json()["conversation"]
    response = bob.get(f'/api/v1/conversations/{created["conversation_id"]}')
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"


def test_api_018_conversation_replay_list_cursor_and_etag(logged_client):
    client, csrf, _ = logged_client
    key = str(uuid4())
    first = client.post(
        "/api/v1/conversations",
        json={"title": None},
        headers=command_headers(csrf, key),
    )
    replay = client.post(
        "/api/v1/conversations",
        json={"title": None},
        headers=command_headers(csrf, key),
    )
    assert first.status_code == replay.status_code == 201
    assert first.json() == replay.json()
    assert replay.headers["idempotency-replayed"] == "true"
    assert first.json()["conversation"]["title"] == "新的对话"
    for number in range(3):
        assert create_conversation(client, csrf, f"conversation-{number}").status_code == 201
    page = client.get("/api/v1/conversations?limit=2").json()
    assert page["has_more"] is True
    assert len(page["items"]) == 2
    # 篡改 payload 部分而不是签名尾部: HMAC 是对 payload 字符串签名, 只要 payload
    # 任一字符改变, 期望签名必然失配 → 400。改签名最后一字符是不确定的——base64
    # 的 padding 位使该改动可能解码出完全相同签名字节, 导致 200。
    payload, signature = page["next_cursor"].split(".", 1)
    tampered = ("X" if payload[0] != "X" else "Y") + payload[1:] + "." + signature
    assert client.get(f"/api/v1/conversations?limit=2&cursor={tampered}").status_code == 400


def test_api_016_and_019_metadata_etag_ignores_message_sequence(logged_client):
    client, csrf, _ = logged_client
    created_response = create_conversation(client, csrf)
    conversation = created_response.json()["conversation"]
    old_etag = created_response.headers["etag"]
    sent = client.post(
        f'/api/v1/conversations/{conversation["conversation_id"]}/messages',
        json={"content": "hello"},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert sent.status_code == 202
    renamed = client.patch(
        f'/api/v1/conversations/{conversation["conversation_id"]}',
        json={"title": "renamed"},
        headers={**command_headers(csrf), "If-Match": old_etag},
    )
    assert renamed.status_code == 200
    stale = client.patch(
        f'/api/v1/conversations/{conversation["conversation_id"]}',
        json={"title": "overwrite"},
        headers={**command_headers(csrf), "If-Match": old_etag},
    )
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "version_conflict"


def test_api_003_004_005_send_idempotency_and_busy(logged_client):
    client, csrf, _ = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    key = str(uuid4())
    first = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "hello\r\nworld"},
        headers=command_headers(csrf, key),
    )
    replay = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "hello\r\nworld"},
        headers=command_headers(csrf, key),
    )
    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    assert replay.headers["idempotency-replayed"] == "true"
    conflict = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "different"},
        headers=command_headers(csrf, key),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"
    busy = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "another"},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert busy.status_code == 409


def test_api_006_messages_are_paginated_in_sequence(logged_client, db):
    client, csrf, account_id = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    for sequence in range(1, 7):
        db.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,"
            "content,sequence,client_request_id) VALUES (%s,%s,%s,'user','accepted',%s,%s,%s)",
            (uuid4(), account_id, conversation_id, f"m{sequence}", sequence, uuid4()),
        )
    db.execute(
        "UPDATE conversations SET last_message_seq=6 WHERE conversation_id=%s",
        (conversation_id,),
    )
    first = client.get(f"/api/v1/conversations/{conversation_id}/messages?limit=2").json()
    second = client.get(
        f'/api/v1/conversations/{conversation_id}/messages?limit=2&cursor={first["next_cursor"]}'
    ).json()
    assert [item["sequence"] for item in first["items"]] == [5, 6]
    assert [item["sequence"] for item in second["items"]] == [3, 4]


def test_api_007_cancel_queued_and_retry_once(logged_client):
    client, csrf, _ = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "cancel me"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    run_id = sent["run"]["run_id"]
    cancelled = client.post(
        f"/api/v1/runs/{run_id}/cancel",
        json={},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["run"]["status"] == "cancelled"
    one = client.post(
        f"/api/v1/runs/{run_id}/retry", json={}, headers=command_headers(csrf, str(uuid4()))
    )
    two = client.post(
        f"/api/v1/runs/{run_id}/retry", json={}, headers=command_headers(csrf, str(uuid4()))
    )
    assert one.status_code == 202
    assert two.status_code == 200
    assert two.headers["resource-reused"] == "true"
    assert one.json()["run"]["run_id"] == two.json()["run"]["run_id"]


def test_b05_fake_executor_completed_refresh_recovery(seed_identity, client_factory):
    seed_identity("alice")
    client = client_factory(fake_enabled=True, fake_mode="success", fake_delay=0.01)
    csrf = login(client)
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "execute"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    run_id = sent["run"]["run_id"]
    snapshot = None
    for _ in range(100):
        snapshot = client.get(f"/api/v1/runs/{run_id}").json()
        if snapshot["run"]["status"] == "completed":
            break
        time.sleep(0.01)
    assert snapshot["run"]["status"] == "completed"
    assert snapshot["assistant_message"]["content"] == "fake completed response"
    detail = client.get(f"/api/v1/conversations/{conversation_id}").json()
    assert detail["active_run"] is None


def test_api_008_running_cancel_reaches_cancelled(seed_identity, client_factory):
    seed_identity("alice")
    client = client_factory(fake_enabled=True, fake_mode="hold")
    csrf = login(client)
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "hold"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    run_id = sent["run"]["run_id"]
    for _ in range(100):
        if client.get(f"/api/v1/runs/{run_id}").json()["run"]["status"] == "running":
            break
        time.sleep(0.01)
    cancelling = client.post(
        f"/api/v1/runs/{run_id}/cancel",
        json={},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert cancelling.status_code == 202
    for _ in range(100):
        snapshot = client.get(f"/api/v1/runs/{run_id}").json()
        if snapshot["run"]["status"] == "cancelled":
            break
        time.sleep(0.01)
    assert snapshot["run"]["status"] == "cancelled"
    assert snapshot["assistant_message"]["status"] == "aborted"


def test_b05_failure_then_retry(seed_identity, client_factory):
    seed_identity("alice")
    client = client_factory(fake_enabled=True, fake_mode="failure", fake_delay=0.01)
    csrf = login(client)
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "fail"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    source_id = sent["run"]["run_id"]
    for _ in range(100):
        snapshot = client.get(f"/api/v1/runs/{source_id}").json()
        if snapshot["run"]["status"] == "failed":
            break
        time.sleep(0.01)
    assert snapshot["run"]["failure"]["code"] == "fake_executor_failure"
    retried = client.post(
        f"/api/v1/runs/{source_id}/retry",
        json={},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert retried.status_code == 202
    assert retried.json()["run"]["retry_of_run_id"] == source_id


def test_message_limits_and_empty_content(logged_client):
    client, csrf, _ = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    empty = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": " \n\t"},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert empty.status_code == 422
    assert empty.json()["error"]["code"] == "empty_message"
    large = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "x" * 32_001},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert large.status_code == 413


def test_concurrent_different_messages_allow_only_one(logged_client):
    client, csrf, _ = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]

    def send(content: str):
        return client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": content},
            headers=command_headers(csrf, str(uuid4())),
        ).status_code

    with ThreadPoolExecutor(max_workers=2) as executor:
        statuses = sorted(executor.map(send, ("one", "two")))
    assert statuses == [202, 409]


def test_api_003_concurrent_same_key_returns_same_resources(logged_client):
    client, csrf, _ = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    key = str(uuid4())

    def send():
        return client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": "same intent"},
            headers=command_headers(csrf, key),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = executor.map(lambda _: send(), range(2))
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert "true" in {
        first.headers.get("idempotency-replayed"),
        second.headers.get("idempotency-replayed"),
    }


def test_api_021_claimed_start_is_not_started_after_direct_cancel(
    logged_client, worker_database_url
):
    client, csrf, account_id = logged_client
    conversation_id = create_conversation(client, csrf).json()["conversation"]["conversation_id"]
    sent = client.post(
        f"/api/v1/conversations/{conversation_id}/messages",
        json={"content": "cancel before dispatcher"},
        headers=command_headers(csrf, str(uuid4())),
    ).json()
    run_id = sent["run"]["run_id"]
    claimed = OutboxService(worker_database_url).claim("api-021", {"start_run"}, 1)
    assert len(claimed) == 1
    cancelled = client.post(
        f"/api/v1/runs/{run_id}/cancel",
        json={},
        headers=command_headers(csrf, str(uuid4())),
    )
    assert cancelled.status_code == 200
    assert CommandService(worker_database_url).start_run(account_id, UUID(run_id)) is False


def test_sse_placeholder_path_uses_structured_404(client_factory):
    client = client_factory()
    response = client.get(f"/api/v1/runs/{uuid4()}/events")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "resource_not_found"
