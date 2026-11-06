"""Application schedule facade with the existing bounded PG use cases."""

from __future__ import annotations

from common.agent_policy import AgentPolicy

from .dispatch_due_work import DispatchDueWork
from .evaluate_schedule import EvaluateSchedule


class WorkScheduleService:
    def __init__(self, database, *, budget_mode="enforce", agent_policy: AgentPolicy | None = None):
        self.database = database
        self.budget_mode = budget_mode
        self.agent_policy = agent_policy or AgentPolicy()

    def run_once(self, limit=50, *, now=None):
        # EvaluateSchedule preserves the original outer retry spanning both PG phases.
        return EvaluateSchedule(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        ).execute(limit, now=now)

    def dispatch_due(self, limit=50, *, now=None):
        return DispatchDueWork(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        ).execute(limit, now=now)

    def occurrence(self, account_id, work_id, version, scheduled_for, *, now=None):
        return EvaluateSchedule(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        ).occurrence(
            account_id,
            work_id,
            version,
            scheduled_for,
            now=now,
        )

    def _occurrence(self, uow, work, schedule, version, scheduled_for, now, missed_from=None):
        return EvaluateSchedule(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        )._occurrence(
            uow,
            work,
            schedule,
            version,
            scheduled_for,
            now,
            missed_from,
        )
