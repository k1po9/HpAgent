"""PG schedule projection and bounded due recovery; no live Work workflow."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from common.agent_policy import AgentPolicy
from persistence.uow import UnitOfWork, retryable_transaction
from work_domain.models import continuation
from work_domain.persistence import WorkRepository, sync_schedule
from work_domain.timing import latest_daily, next_daily

from .dispatch_due_work import DispatchDueWork

logger = logging.getLogger("orchestration.work_schedule")


class EvaluateSchedule:
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
            schedule = uow.execute(
                "SELECT * FROM work_schedules WHERE account_id=%s AND work_id=%s FOR UPDATE",
                (account_id, work_id),
            ).fetchone()
            if schedule is None:
                raise ValueError("schedule unavailable")
            return self._occurrence(uow, work, schedule, version, scheduled_for, now)

    def _occurrence(self, uow, work, schedule, version, scheduled_for, now, missed_from=None):
        prior = uow.execute(
            "SELECT * FROM work_schedule_occurrences WHERE schedule_id=%s "
            "AND schedule_version=%s AND scheduled_for=%s",
            (schedule["schedule_id"], version, scheduled_for),
        ).fetchone()
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
                uow.execute(
                    "UPDATE work_wakeups SET state='skipped' WHERE account_id=%s AND work_id=%s "
                    "AND kind='due' AND state='pending'",
                    (work["account_id"], work["work_id"]),
                )
            key = f"schedule:{schedule['schedule_id']}:{version}:{scheduled_for.astimezone(UTC).isoformat()}"
            wakeup = WorkRepository.wakeup(
                uow, work, key, "due", "scheduled_occurrence", scheduled_for.isoformat()
            )
        row = uow.execute(
            "INSERT INTO work_schedule_occurrences(account_id,work_id,schedule_id,"
            "schedule_version,scheduled_for,wakeup_id,disposition,reason,missed_from) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                work["account_id"],
                work["work_id"],
                schedule["schedule_id"],
                version,
                scheduled_for,
                wakeup,
                disposition,
                reason,
                missed_from,
            ),
        ).fetchone()
        if valid:
            due = (
                next_daily(schedule["timing"], now)
                if schedule["timing"]["kind"] == "daily"
                else None
            )
            uow.execute(
                "UPDATE work_schedules SET next_due_at=%s,last_evaluated_at=%s WHERE schedule_id=%s",
                (due, now, schedule["schedule_id"]),
            )
            # Awaiting receipts/input are never cleared by a timer.
            if work["continuation"]["kind"] in {"ready", "at_time"}:
                uow.execute(
                    "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s",
                    (json.dumps(continuation("ready", "scheduled_occurrence")), work["work_id"]),
                )
        return dict(row)

    @retryable_transaction
    def execute(self, limit=50, *, now=None):
        now = now or datetime.now(UTC)
        with UnitOfWork(self.database) as uow:
            # Reconcile lost/startup synchronization, then evaluate due projections.
            rows = uow.execute(
                "SELECT w.* FROM works w LEFT JOIN work_schedules s USING(account_id,work_id) "
                "WHERE s.schedule_id IS NULL OR s.source_row_version<>w.row_version "
                "OR s.applied_version<>s.schedule_version ORDER BY w.work_id "
                "LIMIT %s FOR UPDATE OF w SKIP LOCKED",
                (limit,),
            ).fetchall()
            for work in rows:
                requirement = WorkRepository.requirement(
                    uow, work["account_id"], work["work_id"], work["current_requirement_revision"]
                )
                sync_schedule(uow, work, requirement)
                uow.execute(
                    "UPDATE work_schedules SET applied_version=schedule_version WHERE work_id=%s",
                    (work["work_id"],),
                )
            rows = uow.execute(
                "SELECT w.* FROM works w JOIN work_schedules s USING(account_id,work_id) "
                "WHERE s.desired_enabled AND s.next_due_at<=%s ORDER BY s.next_due_at "
                "LIMIT %s FOR UPDATE OF w SKIP LOCKED",
                (now, limit),
            ).fetchall()
            for work in rows:
                schedule = uow.execute(
                    "SELECT * FROM work_schedules WHERE work_id=%s FOR UPDATE", (work["work_id"],)
                ).fetchone()
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
