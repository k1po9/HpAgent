from __future__ import annotations

from uuid import uuid4

import psycopg
import pytest

pytestmark = pytest.mark.postgres


def test_runtime_roles_cannot_modify_migration_history(
    database_url: str, worker_database_url: str
) -> None:
    for url in (database_url, worker_database_url):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(
                    "INSERT INTO hpagent.schema_migrations(version) VALUES ('forged')"
                )


def test_api_cannot_forge_workflow_execution(database_url: str) -> None:
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "INSERT INTO hpagent.workflow_executions("
                "workflow_execution_id,account_id,conversation_id,run_id,workflow_id) "
                "VALUES (%s,%s,%s,%s,'forged')",
                (uuid4(), uuid4(), uuid4(), uuid4()),
            )


def test_worker_cannot_modify_web_auth_sessions(worker_database_url: str) -> None:
    with psycopg.connect(worker_database_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute("SELECT * FROM hpagent.web_auth_sessions")
