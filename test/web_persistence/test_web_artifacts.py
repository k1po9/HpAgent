"""Artifact API admission uses a new Work; generation is covered by test_work_integration."""
import asyncio
from uuid import UUID, uuid4

import pytest

from agent_activities.store import StaleFencingToken
from conversation_domain.commands import CommandService
from persistence.uow import UnitOfWork
from run_domain.lifecycle import RunLifecycleService
from web_artifacts.build import ArtifactBuildService
from web_artifacts.services import ArtifactService
from web_domain.errors import IdempotencyConflict, ResourceNotFound
from web_domain.lifecycle import WebRunLifecycleService
from work_domain.commands import WorkCommandService

pytestmark = pytest.mark.postgres


def test_artifact_admission_replays_without_duplicate_mandates(database_url,account_id):
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
    assert len(WorkCommandService(database_url).list(account_id)['items'])==1
    with pytest.raises(ResourceNotFound):
        service.get_version(uuid4(),UUID(created.body['version']['artifact_version_id']))


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["queued", "running"])
async def test_artifact_run_cancel_preserves_provenance_and_rejects_late_publish(
    db, account_id, database_url, worker_database_url, phase
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
    works = WorkCommandService(database_url)
    work = works.list(account_id)["items"][0]
    run_id = UUID(works.advance(account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"]).body["run"]["run_id"])
    entered, release = asyncio.Event(), asyncio.Event()

    class Generator:
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
            # No producer ever claimed this version; a later advance may use it.
            assert version["status"] == "queued" and version["producing_run_id"] is None
        assert lifecycle.finalize_cancelled(run_id).status == "cancelled"
        assert artifacts.get_version(account_id, version_id)["version"] == version
        current = works.get(account_id, UUID(work["work_id"]))["work"]
        assert current["status"] == "active" and current["active_coordinator_run_id"] is None
        assert db.execute("SELECT count(*) FROM work_events WHERE work_id=%s AND event_type='run_cancelled'", (UUID(work["work_id"]),)).fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM work_artifacts WHERE artifact_version_id=%s", (version_id,)).fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM messages WHERE produced_by_run_id=%s", (run_id,)).fetchone()[0] == 0
    finally:
        release.set()
        if build is not None:
            with pytest.raises(StaleFencingToken):
                await asyncio.wait_for(build, 5)
    assert artifacts.get_version(account_id, version_id)["version"]["html"] is None
    if phase == "running":
        # A later advance produces a fresh version; cancelled provenance is not reused.
        following = UUID(works.advance(account_id, UUID(work["work_id"]), str(uuid4()), current["row_version"]).body["run"]["run_id"])
        RunLifecycleService(worker_database_url).start(account_id, following)
        built = await ArtifactBuildService(worker_database_url, Generator()).execute(version_id, run_id=following)
        assert built["artifact_version_id"] != str(version_id)
        fresh = artifacts.get_version(account_id, UUID(built["artifact_version_id"]))["version"]
        assert fresh["status"] == "completed" and fresh["producing_run_id"] == str(following)
        assert artifacts.get_version(account_id, version_id)["version"] == version
