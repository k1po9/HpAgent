"""Explicit Work advancement with its original rejection and waiting responses."""

import json
from datetime import UTC, datetime

from persistence.repositories import AccountRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import ResourceNotFound
from work_domain.errors import WorkConflict
from work_domain.models import continuation
from work_domain.save_policy import SAVE_WAIT_REASONS

from .admit_work_run import AdmitWorkRun, WorkAdmissionContext
from .commands import AdvanceWorkCommand
from .dependencies import WorkCommandDependencies


class AdvanceWork:
    def __init__(self, dependencies: WorkCommandDependencies):
        self.dependencies = dependencies

    @retryable_transaction
    def execute(self, command: AdvanceWorkCommand):
        account_id, work_id, key, row_version = (
            command.account_id,
            command.work_id,
            command.key,
            command.row_version,
        )
        with UnitOfWork(self.dependencies.database) as uow:
            replay, command_id = self.dependencies.claim(
                uow,
                account_id,
                "advance_work",
                key,
                {"work_id": str(work_id), "row_version": row_version},
            )
            if replay:
                return replay
            work = self.dependencies.locked(uow, account_id, work_id, row_version)
            if work["status"] != "active" or work["active_coordinator_run_id"]:
                raise WorkConflict(
                    work,
                    "work_coordinator_busy"
                    if work["active_coordinator_run_id"]
                    else "work_not_active",
                )
            if not AccountRepository().require_active(uow, account_id):
                raise ResourceNotFound()
            requirement = self.dependencies.repo.requirement(
                uow, account_id, work_id, work["current_requirement_revision"]
            )
            from run_domain.lifecycle import RunLifecycleService

            if work["continuation"].get("operation_ref") or RunLifecycleService.unresolved_effect(
                uow, work
            ):
                raise WorkConflict(work, "work_effect_uncertain")
            from work_domain.progress import blocked_reason

            reason = blocked_reason(uow, work)
            if reason or work["continuation"]["reason"] == "budget_exhausted":
                raise WorkConflict(work, reason or "budget_exhausted")
            if (
                work["continuation"]["kind"] in {"awaiting_input", "awaiting_delivery"}
                and work["continuation"]["reason"] not in SAVE_WAIT_REASONS
            ):
                raise WorkConflict(work, "work_awaiting_receipt")
            if work["continuation"].get("due_at") and datetime.fromisoformat(
                work["continuation"]["due_at"]
            ) > datetime.now(UTC):
                return self.dependencies.complete(
                    uow, account_id, "advance_work", key, work_id, 202, reason="not_due"
                )
            wakeup = self.dependencies.repo.wakeup(
                uow, work, f"advance:{command_id}", "advance", "explicit_advance"
            )
            from resources.work_budget import WorkBudgetExhausted

            try:
                run = (
                    AdmitWorkRun(
                        self.dependencies.database,
                        self.dependencies.budget_mode,
                        self.dependencies.agent_policy,
                    )
                    .execute_in_uow(
                        uow,
                        WorkAdmissionContext(work, requirement, wakeup, caller="explicit"),
                    )
                    .run
                )
            except WorkBudgetExhausted:
                uow.execute(
                    "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s",
                    (json.dumps(continuation("blocked", "budget_exhausted")), work_id),
                )
                self.dependencies.repo.event(
                    uow,
                    self.dependencies.repo.get(uow, account_id, work_id),
                    "blocked",
                    command_id=command_id,
                    reason="budget_exhausted",
                )
                return self.dependencies.complete(
                    uow, account_id, "advance_work", key, work_id, 202, reason="budget_exhausted"
                )
            if run is None:
                current = self.dependencies.repo.get(uow, account_id, work_id)
                return self.dependencies.complete(
                    uow,
                    account_id,
                    "advance_work",
                    key,
                    work_id,
                    202,
                    reason=current["continuation"]["reason"],
                )
            work = self.dependencies.repo.get(uow, account_id, work_id)
            self.dependencies.repo.event(
                uow, work, "advanced", command_id=command_id, run_id=run["run_id"]
            )
            return self.dependencies.complete(
                uow, account_id, "advance_work", key, work_id, 202, run=run
            )
