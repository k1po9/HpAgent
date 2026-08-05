from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest

from persistence.migrate import migrate


@pytest.fixture(scope="session")
def database_url() -> str:
    migration_url = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("APP_DATABASE_URL")
    app_url = os.getenv("APP_DATABASE_URL")
    if not migration_url or not app_url:
        pytest.skip("migration and app database URLs are required")
    migrate(migration_url)
    return app_url


@pytest.fixture(scope="session")
def migration_database_url() -> str:
    url = os.getenv("MIGRATION_DATABASE_URL") or os.getenv("APP_DATABASE_URL")
    if not url:
        pytest.skip("MIGRATION_DATABASE_URL is required")
    return url


@pytest.fixture(scope="session")
def worker_database_url() -> str:
    url = os.getenv("WORKER_DATABASE_URL")
    if not url:
        pytest.skip("WORKER_DATABASE_URL is required")
    return url


@pytest.fixture
def db(database_url: str, migration_database_url: str):
    with psycopg.connect(migration_database_url, autocommit=True) as connection:
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
