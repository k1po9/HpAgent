"""PG schedule projection and bounded due recovery; no live Work workflow."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from application.work.events import record_work_event
from common.agent_policy import AgentPolicy
from persistence.uow import UnitOfWork, retryable_transaction
from persistence.wakeups import WakeupRepository
from persistence.work_requirements import WorkRequirementRepository
from persistence.works import WorkRepository as WorkRows
from resources.run_budget import RunBudgetExhausted
from run_domain.lifecycle import RunLifecycleService
from web_domain.errors import DomainError
from work_domain.models import continuation
from work_domain.persistence import WorkRepository

from .admit_work_run import AdmitWorkRun, WorkAdmissionContext

logger = logging.getLogger("orchestration.work_schedule")


class DispatchDueWork:
    def __init__(self, database, *, budget_mode="enforce", agent_policy: AgentPolicy | None = None):
        self.database = database
        self.budget_mode = budget_mode
        self.agent_policy = agent_policy or AgentPolicy()

    @retryable_transaction
    def execute(self, limit=50, *, now=None):
        now = now or datetime.now(UTC)
        count = 0
        with UnitOfWork(self.database) as uow:
            rows = WorkRows.lock_dispatch_candidates(uow, now, limit)
            for work in rows:
                kind = work["continuation"]["kind"]
                due = work["continuation"].get("due_at")
                if kind not in {"ready", "at_time", "retry_after"} or (
                    due and datetime.fromisoformat(due) > now
                ):
                    continue
                if (
                    RunLifecycleService.unresolved_effect(uow, work)
                    or not uow.execute(
                        "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'",
                        (work["account_id"],),
                    ).fetchone()
                ):
                    continue
                requirement = WorkRequirementRepository.get(
                    uow, work["account_id"], work["work_id"], work["current_requirement_revision"]
                )
                wake = WakeupRepository.latest_due(
                    uow, work["work_id"], work["current_requirement_revision"], now
                )
                if wake is None:
                    continue
                # Keep failures local to one Work; other tenants' wakeups still progress.
                try:
                    with uow.connection.transaction():
                        run = (
                            AdmitWorkRun(self.database, self.budget_mode, self.agent_policy)
                            .execute_in_uow(
                                uow,
                                WorkAdmissionContext(
                                    work,
                                    requirement,
                                    wake["wakeup_id"],
                                    caller="scheduler",
                                    now=now,
                                ),
                            )
                            .run
                        )
                        if run is None:
                            continue
                        uow.execute(
                            "INSERT INTO capacity_turns(resource,lane,account_id,last_admitted_at) VALUES ('coordination','background',%s,now()) ON CONFLICT(resource,lane,account_id) DO UPDATE SET last_admitted_at=now()",
                            (work["account_id"],),
                        )
                        current = WorkRepository.get(uow, work["account_id"], work["work_id"])
                        record_work_event(uow, current, "advanced", run_id=run["run_id"])
                    count += 1
                except (ValueError, DomainError, RunBudgetExhausted) as exc:
                    logger.warning("Work admission blocked: %s", type(exc).__name__)
                    WorkRows.set_pending_continuation(
                        uow,
                        json.dumps(
                            continuation(
                                "blocked",
                                "budget_exhausted"
                                if isinstance(exc, RunBudgetExhausted)
                                else "admission_rejected",
                            )
                        ),
                        work["work_id"],
                    )
                    current = WorkRepository.get(uow, work["account_id"], work["work_id"])
                    record_work_event(
                        uow, current, "blocked", reason=current["continuation"]["reason"]
                    )
                except Exception:
                    # The savepoint rolls back this admission only. Keep its wakeup
                    # pending for recovery and let other accounts make progress.
                    logger.exception("Work admission failed: work_id=%s", work["work_id"])
        return count
