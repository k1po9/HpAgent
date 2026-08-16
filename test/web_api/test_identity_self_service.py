from __future__ import annotations

from uuid import uuid4

import pytest

from account.identity_binding_service import IdentityBindingService

pytestmark = pytest.mark.postgres


def _headers(csrf: str) -> dict[str, str]:
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": csrf,
        "Content-Type": "application/json",
    }


def test_register_auto_login_logout_and_database_login(client_factory, db):
    client = client_factory(postgres_credentials=True)
    registered = client.post(
        "/auth/register",
        json={"username": " Alice ", "password": "correct-password"},
    )
    assert registered.status_code == 201
    assert "HttpOnly" in registered.headers["set-cookie"]
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["identities"]["web"]["username"] == "Alice"
    csrf = me.json()["csrf_token"]
    assert client.post("/api/v1/auth/logout", headers=_headers(csrf)).status_code == 204
    assert client.get("/api/v1/me").status_code == 401
    logged_in = client.post(
        "/auth/login",
        json={"username": "ALICE", "password": "correct-password"},
        follow_redirects=False,
    )
    assert logged_in.status_code == 303


def test_register_duplicate_and_password_policy_are_safe(client_factory, db):
    client = client_factory(postgres_credentials=True)
    assert client.post(
        "/auth/register", json={"username": "alice", "password": "correct-password"}
    ).status_code == 201
    duplicate = client.post(
        "/auth/register", json={"username": " ALICE ", "password": "another-password"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "username_already_exists"
    weak = client.post(
        "/auth/register", json={"username": "bob", "password": "short"}
    )
    assert weak.status_code == 422
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


def test_register_reports_success_when_session_creation_fails(client_factory, db):
    client = client_factory(postgres_credentials=True)

    def fail_session_creation(_subject):
        raise RuntimeError("session storage unavailable")

    client.app.state.auth.login = fail_session_creation

    response = client.post(
        "/auth/register",
        json={"username": "alice", "password": "correct-password"},
    )

    assert response.status_code == 201
    assert response.json()["registered"] is True
    assert response.json()["session_established"] is False
    assert "set-cookie" not in response.headers
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0] == 1


def test_qq_challenge_updates_me_without_entering_agent(
    client_factory, db, worker_database_url
):
    client = client_factory(postgres_credentials=True)
    assert client.post(
        "/auth/register", json={"username": "alice", "password": "correct-password"}
    ).status_code == 201
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    created = client.post(
        "/api/v1/identity-bindings/qq/challenges",
        headers=_headers(csrf),
        json={},
    )
    assert created.status_code == 201
    body = created.json()
    IdentityBindingService(
        worker_database_url, b"development-qq-binding-code-pepper"
    ).consume_qq_challenge(
        body["code"], "napcat", "123456789"
    )
    status = client.get(
        f"/api/v1/identity-bindings/qq/challenges/{body['challenge_id']}"
    )
    assert status.json()["status"] == "completed"
    me = client.get("/api/v1/me").json()
    assert me["identities"]["qq"] == {
        "bound": True,
        "channel_type": "napcat",
        "display_subject": "123***789",
    }


def test_existing_qq_account_becomes_web_session_account(
    client_factory, db, worker_database_url
):
    qq_account, qq_binding = uuid4(), uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (qq_account,))
    db.execute(
        "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
        "external_subject_id,normalized_subject_id,verified_at,metadata) "
        "VALUES (%s,%s,'qq','123456','napcat:123456',now(),"
        "'{\"channel_type\":\"napcat\"}'::jsonb)",
        (qq_binding, qq_account),
    )
    client = client_factory(postgres_credentials=True)
    client.post(
        "/auth/register", json={"username": "alice", "password": "correct-password"}
    )
    old_account = client.get("/api/v1/me").json()["account"]["account_id"]
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    challenge = client.post(
        "/api/v1/identity-bindings/qq/challenges",
        headers=_headers(csrf),
        json={},
    ).json()
    result = IdentityBindingService(
        worker_database_url, b"development-qq-binding-code-pepper"
    ).consume_qq_challenge(
        challenge["code"], "napcat", "123456"
    )
    assert result.consolidated is True
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    assert me.json()["account"]["account_id"] == str(qq_account)
    assert me.json()["account"]["account_id"] != old_account
