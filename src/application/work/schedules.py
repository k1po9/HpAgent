"""Synchronize a Work schedule in the caller's existing transaction."""

import json
from datetime import UTC, datetime

from uuid6 import uuid7

from persistence.work_schedules import WorkScheduleRepository
from work_domain.scheduling import plan_schedule
from work_domain.timing import next_daily


def sync_schedule(uow, work, requirement):
    timing = requirement["timing"]
    row = WorkScheduleRepository.lock_owned(uow, work["account_id"], work["work_id"])
    plan = plan_schedule(work, requirement, row)
    due = next_daily(timing, datetime.now(UTC)) if plan.needs_daily_due else plan.due_at
    WorkScheduleRepository.upsert(
        uow,
        uuid7(),
        work["account_id"],
        work["work_id"],
        work["current_requirement_revision"],
        plan.version,
        plan.enabled,
        json.dumps(timing),
        timing["timezone"],
        due,
        work["row_version"],
    )
