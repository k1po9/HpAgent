"""Default Research renewal protects stages longer than their initial lease."""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from support.research_driver import ResearchDriver as ResearchActivities
from support.work_fixtures import accept_research, advance_work

from agent_activities.store import AgentDataStore
from agent_workflows.lifecycle_contracts import SegmentInput
from orchestration.research_workflow import ResearchWorkflowInput
from persistence.uow import UnitOfWork
from research_domain.context import research_request
from run_domain.execution import LeaseConflict
from work_domain.commands import WorkCommandService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_default_research_lease_is_renewed_past_initial_expiry(
    database_url, worker_database_url, account_id,
):
    commands = WorkCommandService(database_url)
    work = accept_research(commands, account_id, str(uuid4()), "Lease", "Hold current ownership")
    run_id = UUID(advance_work(commands, account_id, UUID(work.body["work"]["work_id"]),
                               str(uuid4())).body["run"]["run_id"])
    research = ResearchActivities(worker_database_url, None, None, None, None)
    await research.prepare_research_activity(ResearchWorkflowInput(1, str(run_id)))
    started = asyncio.Event()
    finish = asyncio.Event()

    async def action():
        started.set()
        await finish.wait()
        return {"item_count": 1}

    local = research.local(ResearchWorkflowInput(1, str(run_id)))
    reset = research_request.set(local)
    try:
        task = asyncio.create_task(research.research._run_stage(run_id, "LeaseProbe", action))
    finally:
        research_request.reset(reset)
    try:
        await asyncio.wait_for(started.wait(), 10)
        with UnitOfWork(worker_database_url) as uow:
            lease = uow.execute("SELECT execution_id,fencing_token,lease_expires_at "
                                "FROM execution_attempt_leases WHERE run_id=%s AND execution_id=%s", (run_id, local.execution_id)).fetchone()
        # Real elapsed time, with the production default and no expiry updates from the test.
        await asyncio.sleep(35)
        with UnitOfWork(worker_database_url) as uow:
            current = uow.execute("SELECT fencing_token,lease_expires_at,"
                                  "lease_expires_at>clock_timestamp() AS active "
                                  "FROM execution_attempt_leases WHERE run_id=%s AND execution_id=%s", (run_id, local.execution_id)).fetchone()
        assert current["fencing_token"] == lease["fencing_token"]
        assert current["active"] and current["lease_expires_at"] > lease["lease_expires_at"]
        rival = SegmentInput(1, str(run_id), str(account_id), "competing-stage",
                             execution_id=str(lease["execution_id"]))
        with pytest.raises(LeaseConflict):
            await asyncio.to_thread(AgentDataStore(worker_database_url).acquire_segment, rival)
        finish.set()
        assert (await asyncio.wait_for(task, 10))["item_count"] == 1
    finally:
        finish.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
