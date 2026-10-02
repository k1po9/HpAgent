"""Artifact publication from a producing execution, independently of Message."""

from uuid6 import uuid7

from agent_activities.store import AgentDataStore
from run_domain.lifecycle import RunLifecycleService


def publish(uow, run_id, artifact_id, version_id, html, *, operation_id, title="", markdown=None):
    AgentDataStore._assert_fence(uow)
    identity = uow.execute("SELECT account_id FROM runs WHERE run_id=%s", (run_id,)).fetchone()
    run = RunLifecycleService.lock(uow, identity["account_id"], run_id)
    if run["status"] != "running":
        raise ValueError("Artifact producing Run is not active")
    work = RunLifecycleService.check_work(uow, run) if run["work_id"] else None
    execution = uow.execute(
        "SELECT execution_id FROM execution_operations WHERE account_id=%s AND run_id=%s AND operation_id=%s",
        (run["account_id"], run_id, operation_id),
    ).fetchone()
    if not execution:
        raise ValueError("Artifact operation must be registered")
    uow.execute(
        "INSERT INTO artifacts(artifact_id,account_id,title) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
        (artifact_id, run["account_id"], title[:200]),
    )
    uow.execute(
        "INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,version,status,html,started_at,completed_at,"
        "producing_run_id,producing_execution_id,producing_operation_id,source_markdown) VALUES (%s,%s,%s,1,'completed',%s,now(),now(),%s,%s,%s,%s) "
        "ON CONFLICT(artifact_version_id) DO NOTHING",
        (
            version_id,
            artifact_id,
            run["account_id"],
            html,
            run_id,
            execution["execution_id"],
            operation_id,
            markdown,
        ),
    )
    version = uow.execute(
        "SELECT * FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s FOR UPDATE",
        (run["account_id"], version_id),
    ).fetchone()
    if (
        not version
        or version["artifact_id"] != artifact_id
        or version["producing_run_id"] != run_id
        or version["producing_operation_id"] != operation_id
    ):
        raise ValueError("Artifact publication provenance conflict")
    if version["status"] != "completed":
        uow.execute(
            "UPDATE artifact_versions SET status='completed',html=%s,completed_at=now(),failure_code=NULL,failure_message=NULL,updated_at=now() WHERE artifact_version_id=%s",
            (html, version_id),
        )
    elif version["html"] != html:
        raise ValueError("Artifact content conflict")
    if work:
        uow.execute(
            "INSERT INTO work_artifacts(reference_id,account_id,work_id,artifact_version_id,source_requirement_revision,role) "
            "VALUES (%s,%s,%s,%s,%s,'deliverable') ON CONFLICT DO NOTHING",
            (uuid7(), run["account_id"], run["work_id"], version_id, run["requirement_revision"]),
        )
