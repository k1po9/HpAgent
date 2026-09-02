from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from temporalio import activity
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleIntervalSpec,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.worker import Worker
from uuid6 import uuid7

from orchestration.research_workflow import (
    ResearchScheduleInput,
    ResearchTaskScheduleWorkflow,
)
from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE
from persistence.uow import UnitOfWork
from research_domain.services import ResearchTaskCommandService, TaskBusy

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal, pytest.mark.postgres]


async def test_real_schedule_fires_through_task_command_budget_and_outbox(
    database_url, worker_database_url, account_id
):
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")
    commands = ResearchTaskCommandService(database_url)
    task = commands.create_task(account_id, str(uuid7()), "Scheduled", "Daily changes")
    task_id = UUID(task.body["task_id"])

    @activity.defn(name="trigger_scheduled_research_activity")
    async def trigger(request):
        try:
            result = await asyncio.to_thread(
                ResearchTaskCommandService(worker_database_url).trigger_task,
                UUID(request["account_id"]), UUID(request["task_id"]),
                f"schedule-test:{request['fire_id']}",
            )
        except TaskBusy:
            return {"created": False, "reason": "TaskBusy"}
        return {"created": True, "run_id": result.body["run_id"]}

    client = await Client.connect(temporal_host, namespace="hpagent-research-test")
    schedule_id = f"research-acceptance-{uuid4()}"
    worker = Worker(
        client, task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[ResearchTaskScheduleWorkflow], activities=[trigger],
    )
    async with worker:
        await client.create_schedule(
            schedule_id,
            Schedule(
                action=ScheduleActionStartWorkflow(
                    ResearchTaskScheduleWorkflow.run,
                    ResearchScheduleInput(1, str(account_id), str(task_id), 1),
                    id=f"{schedule_id}-fire", task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                ),
                spec=ScheduleSpec(intervals=[ScheduleIntervalSpec(every=timedelta(seconds=2))]),
                policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
            ),
        )
        try:
            for _ in range(20):
                await asyncio.sleep(0.5)
                with UnitOfWork(database_url) as uow:
                    row = uow.execute(
                        "SELECT r.run_id,o.event_type,b.limits FROM runs r "
                        "JOIN outbox_events o ON o.run_id=r.run_id "
                        "JOIN run_budgets b ON b.run_id=r.run_id "
                        "WHERE r.task_id=%s ORDER BY r.created_at DESC LIMIT 1", (task_id,),
                    ).fetchone()
                if row is not None:
                    break
            assert row is not None
            assert row["event_type"] == "start_research_run"
            assert row["limits"]["research_iterations"] == 3
        finally:
            await client.get_schedule_handle(schedule_id).delete()
