from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from persistence.migrate import migrate


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.getenv("APP_DATABASE_URL")
    if not url:
        pytest.skip("APP_DATABASE_URL is required for real PostgreSQL contract tests")
    migrate(url)
    return url


@pytest.fixture
def db(database_url: str):
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SET search_path TO hpagent, public")
        for table in ("outbox_events", "idempotency_commands", "workflow_executions", "messages", "runs", "sessions", "web_auth_sessions", "identity_bindings", "conversations", "accounts"):
            connection.execute(f"TRUNCATE {table} CASCADE")
        yield connection


def account(db):
    value = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (value,))
    return value


@pytest.fixture
def account_id(db):
    return account(db)
