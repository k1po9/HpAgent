"""Reconcile persisted Research Task schedules into Temporal Schedules."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from temporalio.client import (
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
    ScheduleState,
)

from orchestration.research_workflow import (
    RESEARCH_WORKFLOW_SCHEMA_VERSION,
    ResearchScheduleInput,
    ResearchTaskScheduleWorkflow,
)
from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE
from persistence.uow import UnitOfWork, retryable_transaction

logger = logging.getLogger("HpAgent.ResearchSchedule")


class ResearchScheduleManager:
    def __init__(self, database: object, temporal_client: Any) -> None:
        self.database = database
        self.temporal_client = temporal_client

    @staticmethod
    def schedule_id(task_id: object) -> str:
        return f"hpagent-research-task-{task_id}"

    @retryable_transaction
    def _pending(self) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            return list(uow.execute(
                "SELECT task_id,account_id,schedule_type,schedule_timezone,"
                "schedule_expression,schedule_enabled,schedule_version "
                "FROM tasks WHERE schedule_applied_version<schedule_version "
                "ORDER BY task_id LIMIT 100"
            ).fetchall())

    @retryable_transaction
    def _mark_applied(self, task_id: object, version: int) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE tasks SET schedule_applied_version=%s,updated_at=now() "
                "WHERE task_id=%s AND schedule_version=%s",
                (version, task_id, version),
            )

    async def reconcile_once(self) -> int:
        rows = await asyncio.to_thread(self._pending)
        for row in rows:
            schedule_id = self.schedule_id(row["task_id"])
            handle = self.temporal_client.get_schedule_handle(schedule_id)
            try:
                await handle.delete()
            except Exception:
                pass
            if bool(row["schedule_enabled"]):
                hour, minute = str(row["schedule_expression"]).split(":", 1)
                version = int(row["schedule_version"])
                await self.temporal_client.create_schedule(
                    schedule_id,
                    Schedule(
                        action=ScheduleActionStartWorkflow(
                            ResearchTaskScheduleWorkflow.run,
                            ResearchScheduleInput(
                                RESEARCH_WORKFLOW_SCHEMA_VERSION,
                                str(row["account_id"]),
                                str(row["task_id"]),
                                version,
                            ),
                            id=f"{schedule_id}-fire",
                            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                        ),
                        spec=ScheduleSpec(
                            cron_expressions=[f"{int(minute)} {int(hour)} * * *"],
                            time_zone_name=str(row["schedule_timezone"]),
                        ),
                        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP),
                        state=ScheduleState(note=f"research-task-version:{version}"),
                    ),
                )
            await asyncio.to_thread(
                self._mark_applied, row["task_id"], int(row["schedule_version"])
            )
        return len(rows)


async def run_research_schedule_reconciler_loop(
    manager: ResearchScheduleManager, interval_seconds: float = 5.0
) -> None:
    while True:
        try:
            await manager.reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Research Schedule reconciliation failed")
        await asyncio.sleep(interval_seconds)
