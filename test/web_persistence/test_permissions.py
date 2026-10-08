from __future__ import annotations

from uuid import UUID, uuid4

import psycopg
import pytest

from conversation_domain.commands import CommandService
from web_artifacts.services import ArtifactService
from web_domain.errors import ResourceNotFound

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
    db, account_id, database_url: str, worker_database_url: str
) -> None:
    commands = CommandService(database_url)
    cid = UUID(commands.create_conversation(account_id, str(uuid4())).body["conversation"]["conversation_id"])
    run = UUID(commands.send_message(account_id, cid, str(uuid4()), "source").body["run_id"])
    commands.start_run(account_id, run)
    commands.complete_run(account_id, run, "# Permission source")
    message = db.execute("SELECT message_id FROM messages WHERE produced_by_run_id=%s", (run,)).fetchone()[0]
    service = ArtifactService(database_url)
    created = service.create_artifact(account_id, message, str(uuid4()))
    artifact_id = UUID(created.body["artifact"]["artifact_id"])
    version_id = UUID(created.body["version"]["artifact_version_id"])
    assert service.create_version(account_id, artifact_id, str(uuid4()), "next").response_status == 202
    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
            connection.execute(
                "UPDATE hpagent.artifact_versions SET instruction='forged' WHERE artifact_version_id=%s",
                (version_id,),
            )
        assert denied.value.sqlstate == "42501"
    with psycopg.connect(worker_database_url) as connection:
        # Research publication has no source Chat; Worker must create both rows.
        produced_artifact, produced_version = uuid4(), uuid4()
        connection.execute("INSERT INTO hpagent.artifacts(artifact_id,account_id,title) VALUES (%s,%s,'Research')",
                           (produced_artifact, account_id))
        connection.execute("INSERT INTO hpagent.artifact_versions(artifact_version_id,artifact_id,account_id,version) VALUES (%s,%s,%s,1)",
                           (produced_version, produced_artifact, account_id))
        connection.execute("UPDATE hpagent.artifact_versions SET instruction='worker' WHERE artifact_version_id=%s", (produced_version,))
        connection.commit()
    for url in (database_url, worker_database_url):
        for table, key, value in (("artifacts", "artifact_id", artifact_id),
                                  ("artifact_versions", "artifact_version_id", version_id)):
            with psycopg.connect(url) as connection:
                with pytest.raises(psycopg.errors.InsufficientPrivilege) as denied:
                    connection.execute(f"DELETE FROM hpagent.{table} WHERE {key}=%s", (value,))
                assert denied.value.sqlstate == "42501"
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    for action in (lambda: service.get_version(other, version_id),
                   lambda: service.create_version(other, artifact_id, str(uuid4()), "foreign"),
                   lambda: service.create_artifact(other, message, str(uuid4()))):
        with pytest.raises(ResourceNotFound):
            action()
    for url in (database_url, worker_database_url):
        with psycopg.connect(url) as connection:
            with pytest.raises(psycopg.errors.ForeignKeyViolation) as invalid:
                connection.execute("INSERT INTO hpagent.artifact_versions(artifact_version_id,artifact_id,account_id,version) VALUES (%s,%s,%s,99)",
                                   (uuid4(), artifact_id, other))
            assert invalid.value.sqlstate == "23503"
