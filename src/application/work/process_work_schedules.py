"""Synchronize and evaluate Work schedules, then dispatch due work in a separate transaction."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from application.work.schedules import sync_schedule
from common.agent_policy import AgentPolicy
from persistence.uow import UnitOfWork, retryable_transaction
from persistence.wakeups import WakeupRepository
from persistence.work_requirements import WorkRequirementRepository
from persistence.work_schedules import WorkScheduleRepository
from persistence.works import WorkRepository
from work_domain.models import continuation
from work_domain.timing import latest_daily, next_daily

from .dispatch_due_work import DispatchDueWork

logger = logging.getLogger("orchestration.work_schedule")


class ProcessWorkSchedules:
    def __init__(self, database, *, budget_mode="enforce", agent_policy: AgentPolicy | None = None):
        self.database = database
        self.dispatcher = DispatchDueWork(
            database, budget_mode=budget_mode, agent_policy=agent_policy
        )

    @retryable_transaction
    def occurrence(self, account_id, work_id, version, scheduled_for, *, now=None):
        """Stable callback API; disabled/old versions leave a skipped fact."""
        now = now or datetime.now(UTC)
        if scheduled_for.tzinfo is None or scheduled_for > now:
            raise ValueError("occurrence must be an aware due UTC instant")
        with UnitOfWork(self.database) as uow:
            work = WorkRepository.get(uow, account_id, work_id, lock=True)
            schedule = WorkScheduleRepository.lock_owned(uow, account_id, work_id)
            if schedule is None:
                raise ValueError("schedule unavailable")
            return self._occurrence(uow, work, schedule, version, scheduled_for, now)

    def _occurrence(self, uow, work, schedule, version, scheduled_for, now, missed_from=None):
        prior = WorkScheduleRepository.occurrence(
            uow, schedule["schedule_id"], version, scheduled_for
        )
        if prior:
            return dict(prior)
        valid = (
            schedule["desired_enabled"]
            and schedule["schedule_version"] == version
            and work["status"] == "active"
        )
        if valid:
            timing = schedule["timing"]
            expected = (
                datetime.fromisoformat(timing["due_at"])
                if timing["kind"] == "once"
                else latest_daily(timing, scheduled_for)
            )
            if expected != scheduled_for:
                raise ValueError("occurrence does not match schedule")
        disposition = "pending" if valid else "skipped"
        reason = "due" if valid else "disabled_or_superseded"
        wakeup = None
        if valid:
            # Daily catch-up retains at most the newest pending occurrence.
            if schedule["timing"]["kind"] == "daily":
                WakeupRepository.skip_pending_due(uow, work["account_id"], work["work_id"])
            key = f"schedule:{schedule['schedule_id']}:{version}:{scheduled_for.astimezone(UTC).isoformat()}"
            wakeup = WakeupRepository.enqueue(
                uow, work, key, "due", "scheduled_occurrence", scheduled_for.isoformat()
            )
        row = WorkScheduleRepository.append_occurrence(
            uow,
            work["account_id"],
            work["work_id"],
            schedule["schedule_id"],
            version,
            scheduled_for,
            wakeup,
            disposition,
            reason,
            missed_from,
        )
        if valid:
            due = (
                next_daily(schedule["timing"], now)
                if schedule["timing"]["kind"] == "daily"
                else None
            )
            WorkScheduleRepository.update_due(uow, due, now, schedule["schedule_id"])
            # Awaiting receipts/input are never cleared by a timer.
            if work["continuation"]["kind"] in {"ready", "at_time"}:
                WorkRepository.set_schedule_ready(
                    uow, json.dumps(continuation("ready", "scheduled_occurrence")), work["work_id"]
                )
        return dict(row)

    @retryable_transaction
    def execute(self, limit=50, *, now=None):
        now = now or datetime.now(UTC)
        with UnitOfWork(self.database) as uow:
            # Reconcile lost/startup synchronization, then evaluate due projections.
            rows = WorkScheduleRepository.lock_unsynchronized(uow, limit)
            for work in rows:
                requirement = WorkRequirementRepository.get(
                    uow, work["account_id"], work["work_id"], work["current_requirement_revision"]
                )
                sync_schedule(uow, work, requirement)
                WorkScheduleRepository.mark_applied(uow, work["work_id"])
            rows = WorkScheduleRepository.lock_due(uow, now, limit)
            for work in rows:
                schedule = WorkScheduleRepository.lock_for_work(uow, work["work_id"])
                due = schedule["next_due_at"]
                latest = (
                    latest_daily(schedule["timing"], now)
                    if schedule["timing"]["kind"] == "daily"
                    else due
                )
                self._occurrence(
                    uow,
                    work,
                    schedule,
                    schedule["schedule_version"],
                    latest,
                    now,
                    due if latest > due else None,
                )
        return self.dispatcher.execute(limit, now=now)
