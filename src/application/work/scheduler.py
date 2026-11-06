"""Application schedule facade with the existing bounded PG use cases."""

from __future__ import annotations

from common.agent_policy import AgentPolicy

from .dispatch_due_work import DispatchDueWork
from .process_work_schedules import ProcessWorkSchedules


class WorkScheduleService:
    def __init__(self, database, *, budget_mode="enforce", agent_policy: AgentPolicy | None = None):
        self.database = database
        self.budget_mode = budget_mode
        self.agent_policy = agent_policy or AgentPolicy()

    def run_once(self, limit=50, *, now=None):
        # ProcessWorkSchedules preserves the original outer retry spanning both PG phases.
        return ProcessWorkSchedules(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        ).execute(limit, now=now)

    def dispatch_due(self, limit=50, *, now=None):
        return DispatchDueWork(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        ).execute(limit, now=now)

    def occurrence(self, account_id, work_id, version, scheduled_for, *, now=None):
        return ProcessWorkSchedules(
            self.database, budget_mode=self.budget_mode, agent_policy=self.agent_policy
        ).occurrence(
            account_id,
            work_id,
            version,
            scheduled_for,
            now=now,
        )
