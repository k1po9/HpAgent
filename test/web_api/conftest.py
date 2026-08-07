from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient

from persistence.migrate import migrate
from web_api.app import create_app
from web_api.config import WebApiSettings


@pytest.fixture(scope="session")
def redis_url() -> str:
    value = os.getenv("REDIS_URL")
    if not value:
        pytest.skip("REDIS_URL is required")
    return value


@pytest.fixture
def sync_redis(redis_url: str):
    import redis

    client = redis.Redis.from_url(redis_url, decode_responses=False)
    yield client
    client.close()


class TestCredentials:
    def verify(self, username: str, password: str) -> str | None:
        if password != "correct-password":
            return None
        return username.strip().casefold()


@pytest.fixture(scope="session")
def migration_database_url() -> str:
    value = os.getenv("MIGRATION_DATABASE_URL")
    if not value:
        pytest.skip("MIGRATION_DATABASE_URL is required")
    migrate(value)
    return value


@pytest.fixture(scope="session")
def database_url(migration_database_url: str) -> str:
    value = os.getenv("APP_DATABASE_URL")
    if not value:
        pytest.skip("APP_DATABASE_URL is required")
    return value


@pytest.fixture(scope="session")
def worker_database_url() -> str:
    value = os.getenv("WORKER_DATABASE_URL")
    if not value:
        pytest.skip("WORKER_DATABASE_URL is required")
    return value


@pytest.fixture
def db(migration_database_url: str):
    with psycopg.connect(migration_database_url, autocommit=True) as connection:
        connection.execute("SET search_path TO hpagent, public")
        for table in (
            "outbox_events", "idempotency_commands", "workflow_executions",
            "messages", "runs", "sessions", "web_auth_sessions",
            "identity_bindings", "conversations", "accounts",
        ):
            connection.execute(f"TRUNCATE {table} CASCADE")
        yield connection


@pytest.fixture
def seed_identity(db):
    def seed(subject: str) -> UUID:
        account_id, binding_id = uuid4(), uuid4()
        db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account_id,))
        db.execute(
            "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
            "external_subject_id,normalized_subject_id,verified_at) "
            "VALUES (%s,%s,'web',%s,%s,now())",
            (binding_id, account_id, subject, subject.strip().casefold()),
        )
        return account_id

    return seed


@pytest.fixture
def client_factory(database_url: str, worker_database_url: str):
    clients: list[TestClient] = []

    def factory(
        *,
        fake_enabled: bool = False,
        fake_mode: str = "hold",
        fake_delay: float = 0.02,
        redis_url: str | None = None,
        sse_keepalive_seconds: float = 15.0,
        sse_max_connections: int = 256,
        sse_handshake_buffer_events: int = 256,
        sse_handshake_buffer_bytes: int = 1 * 1024 * 1024,
    ) -> TestClient:
        settings = WebApiSettings(
            database_url=database_url,
            worker_database_url=worker_database_url,
            public_origin="https://testserver",
            cursor_signing_keys={"test": b"cursor-test-key-32-bytes-minimum!!"},
            active_cursor_key_id="test",
            session_token_pepper=b"session-test-key-32-bytes-minimum!",
            csrf_signing_key=b"csrf-test-key-32-bytes-minimum!!!!",
            environment="test",
            fake_executor_enabled=fake_enabled,
            fake_executor_mode=fake_mode,
            fake_executor_delay_seconds=fake_delay,
            fake_executor_content="fake completed response",
            cookie_secure=True,
            redis_url=redis_url,
            sse_keepalive_seconds=sse_keepalive_seconds,
            sse_max_connections=sse_max_connections,
            sse_handshake_buffer_events=sse_handshake_buffer_events,
            sse_handshake_buffer_bytes=sse_handshake_buffer_bytes,
            terminal_publisher_poll_seconds=0.1,
        )
        client = TestClient(
            create_app(settings, TestCredentials()), base_url="https://testserver"
        )
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in reversed(clients):
        client.__exit__(None, None, None)


def login(client: TestClient, username: str = "alice") -> str:
    response = client.post(
        "/auth/login",
        json={"username": username, "password": "correct-password", "return_to": "/"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    me = client.get("/api/v1/me")
    assert me.status_code == 200
    return str(me.json()["csrf_token"])


@pytest.fixture
def logged_client(seed_identity, client_factory) -> Iterator[tuple[TestClient, str, UUID]]:
    account_id = seed_identity("alice")
    client = client_factory()
    csrf = login(client)
    yield client, csrf, account_id
