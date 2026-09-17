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


def test_worker_can_read_identity_bindings_after_009(worker_database_url: str) -> None:
    """009 迁移后 Worker 必须能只读 identity_bindings（Phase F 统一身份）。"""
    with psycopg.connect(worker_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute("SELECT * FROM identity_bindings LIMIT 1")


def test_worker_has_narrow_identity_control_plane_write(worker_database_url: str) -> None:
    """QQ ingress control-plane may write bindings, but not create accounts."""
    with psycopg.connect(worker_database_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute("UPDATE identity_bindings SET updated_at=updated_at WHERE false")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "INSERT INTO accounts(account_id) VALUES (%s)", (uuid4(),)
            )


def test_artifact_runtime_permissions_are_split_by_responsibility(
    database_url: str, worker_database_url: str
) -> None:
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "UPDATE hpagent.artifact_versions SET status='completed' WHERE false"
            )
    with psycopg.connect(worker_database_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute(
                "INSERT INTO hpagent.artifacts(artifact_id,account_id,conversation_id,"
                "source_message_id) VALUES (%s,%s,%s,%s)",
                (uuid4(), uuid4(), uuid4(), uuid4()),
            )
