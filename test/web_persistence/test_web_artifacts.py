"""Artifact API uses a finite Chat invocation with independent immutable provenance."""
import asyncio
from uuid import UUID, uuid4

import pytest
from temporalio.exceptions import ApplicationError

from conversation_domain.commands import CommandService
from persistence.uow import UnitOfWork
from run_domain.execution import StaleFencingToken
from run_domain.lifecycle import RunLifecycleService
from web_artifacts.build import ArtifactBuildService
from web_artifacts.services import ArtifactService
from web_domain.errors import ConversationBusy, IdempotencyConflict, ResourceNotFound
from web_domain.lifecycle import WebRunLifecycleService
from work_domain.commands import WorkCommandService

pytestmark = pytest.mark.postgres


def test_artifact_admission_replays_without_duplicate_mandates(db,database_url,account_id):
    chat=CommandService(database_url)
    cid=UUID(chat.create_conversation(account_id,str(uuid4())).body['conversation']['conversation_id'])
    run=UUID(chat.send_message(account_id,cid,str(uuid4()),'Source').body['run_id'])
    chat.start_run(account_id,run)
    chat.complete_run(account_id,run,'# Answer')
    with UnitOfWork(database_url) as uow:
        message=uow.execute('SELECT message_id FROM messages WHERE produced_by_run_id=%s',(run,)).fetchone()['message_id']
    service=ArtifactService(database_url)
    key=str(uuid4())
    created=service.create_artifact(account_id,message,key)
    assert service.create_artifact(account_id,message,key).replayed
    with pytest.raises(IdempotencyConflict):
        service.create_artifact(account_id,message,key,'Changed')
    assert len(WorkCommandService(database_url).list(account_id)['items'])==0
    with pytest.raises(ResourceNotFound):
        service.get_version(uuid4(),UUID(created.body['version']['artifact_version_id']))
    budget = db.execute(
        "SELECT mode,policy_version,limits,final_response_reserve_tokens "
        "FROM run_budgets b JOIN runs r USING(run_id) WHERE r.executor_key='artifact_html'"
    ).fetchone()
    assert budget[0:2] == ("observe", "file-p0-v1")
    assert budget[2]["model_calls"] == 20 and budget[3] == 1024
    tables = ("artifacts", "artifact_versions", "messages", "runs", "run_budgets",
              "outbox_events", "idempotency_commands")
    before = [db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables]
    sequence = db.execute("SELECT last_message_seq FROM conversations WHERE conversation_id=%s", (cid,)).fetchone()
    with pytest.raises(ConversationBusy):
        service.create_artifact(account_id, message, str(uuid4()))
    assert [db.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in tables] == before
    assert db.execute("SELECT last_message_seq FROM conversations WHERE conversation_id=%s", (cid,)).fetchone() == sequence


def test_html_retry_preserves_source_executor_and_frozen_snapshot(
    db, account_id, database_url, worker_database_url,
):
    from common.agent_policy import AgentPolicy

    chat = CommandService(database_url)
    worker = CommandService(worker_database_url)
    cid = UUID(chat.create_conversation(account_id, str(uuid4()))["conversation_id"])
    source = UUID(chat.send_message(account_id, cid, str(uuid4()), "source")["run_id"])
    worker.start_run(account_id, source)
    worker.complete_run(account_id, source, "# HTML source")
    message = db.execute("SELECT message_id FROM messages WHERE produced_by_run_id=%s", (source,)).fetchone()[0]
    artifact = ArtifactService(database_url).create_artifact(account_id, message, str(uuid4()))
    html_run = db.execute("SELECT run_id FROM runs WHERE executor_key='artifact_html'").fetchone()[0]
    frozen = db.execute(
        "SELECT strategy_kind,executor_key,agent_strategy,input_snapshot FROM runs WHERE run_id=%s",
        (html_run,),
    ).fetchone()
    assert frozen == ("fixed_workflow", "artifact_html", None, {
        "schema_version": 1, "artifact_version_id": artifact["version"]["artifact_version_id"],
    })
    worker.fail_run(account_id, html_run, "test_failure")
    changed_policy = CommandService(database_url, agent_policy=AgentPolicy(max_tool_turns=17))
    retried = changed_policy.retry_run(account_id, html_run, str(uuid4()))
    assert retried.response_status == 202
    assert db.execute(
        "SELECT strategy_kind,executor_key,agent_strategy,input_snapshot FROM runs WHERE run_id=%s",
        (UUID(retried["run_id"]),),
    ).fetchone() == frozen


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "running"])
async def test_artifact_run_cancel_preserves_provenance_and_rejects_late_publish(
    db, account_id, database_url, worker_database_url, phase, file_store_root
):
    commands = CommandService(database_url)
    cid = UUID(commands.create_conversation(account_id, str(uuid4())).body["conversation"]["conversation_id"])
    source = UUID(commands.send_message(account_id, cid, str(uuid4()), "source").body["run_id"])
    commands.start_run(account_id, source)
    commands.complete_run(account_id, source, "# Source")
    message = db.execute("SELECT message_id FROM messages WHERE produced_by_run_id=%s", (source,)).fetchone()[0]
    artifacts = ArtifactService(database_url)
    created = artifacts.create_artifact(account_id, message, str(uuid4()))
    version_id = UUID(created.body["version"]["artifact_version_id"])
    run_id = db.execute("SELECT run_id FROM runs WHERE account_id=%s AND executor_key='artifact_html'", (account_id,)).fetchone()[0]
    entered, release = asyncio.Event(), asyncio.Event()

    class Generator:
        read_timeout_seconds = 90
        async def generate(self, **kwargs):
            entered.set()
            await release.wait()
            return "<html>late result</html>"

    build = None
    if phase == "running":
        RunLifecycleService(worker_database_url).start(account_id, run_id)
        build = asyncio.create_task(ArtifactBuildService(worker_database_url, Generator()).execute(version_id, run_id=run_id))
        await asyncio.wait_for(entered.wait(), 5)
    try:
        cancelled = commands.cancel_run(account_id, run_id, str(uuid4()))
        assert cancelled.body["run"]["status"] == ("cancelling" if phase == "running" else "cancelled")
        lifecycle = WebRunLifecycleService(worker_database_url)
        assert lifecycle.finalize_cancelled(run_id).status == "cancelled"
        version = artifacts.get_version(account_id, version_id)["version"]
        if phase == "running":
            assert version["status"] == "failed"
            assert version["failure"]["code"] == "artifact_cancelled"
            assert version["producing_run_id"] == str(run_id)
            assert version["producing_execution_id"] and version["producing_operation_id"]
        else:
            # Cancellation is visible even before any producer claimed the queued version.
            assert version["status"] == "failed" and version["producing_run_id"] is None
        assert lifecycle.finalize_cancelled(run_id).status == "cancelled"
        assert artifacts.get_version(account_id, version_id)["version"] == version
        assert db.execute("SELECT count(*) FROM works WHERE account_id=%s", (account_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM work_artifacts WHERE artifact_version_id=%s", (version_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM messages WHERE produced_by_run_id=%s", (run_id,)).fetchone()[0] == 1
    finally:
        release.set()
        if build is not None:
            with pytest.raises((StaleFencingToken, ApplicationError)):
                await asyncio.wait_for(build, 5)
    assert artifacts.get_version(account_id, version_id)["version"]["html"] is None
    if phase == "running":
        # A later advance produces a fresh version; cancelled provenance is not reused.
        next_version = artifacts.create_version(account_id, UUID(created.body["artifact"]["artifact_id"]), str(uuid4()), "Retry with a new command").body["version"]
        following = db.execute("SELECT run_id FROM runs WHERE account_id=%s AND status='queued'", (account_id,)).fetchone()[0]
        RunLifecycleService(worker_database_url).start(account_id, following)
        built = await ArtifactBuildService(worker_database_url, Generator()).execute(UUID(next_version["artifact_version_id"]), run_id=following)
        assert built["artifact_version_id"] != str(version_id)
        fresh = artifacts.get_version(account_id, UUID(built["artifact_version_id"]))["version"]
        assert fresh["status"] == "completed" and fresh["producing_run_id"] == str(following)
        assert artifacts.get_version(account_id, version_id)["version"] == version
