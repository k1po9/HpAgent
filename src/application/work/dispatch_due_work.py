"""PG schedule projection and bounded due recovery; no live Work workflow."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from common.agent_policy import AgentPolicy
from persistence.uow import UnitOfWork, retryable_transaction
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
            rows = uow.execute(
                "WITH eligible AS (SELECT w.work_id,w.account_id,row_number() OVER (PARTITION BY w.account_id ORDER BY w.work_id) AS tenant_position "
                "FROM works w WHERE w.status='active' AND w.active_coordinator_run_id IS NULL AND EXISTS "
                "(SELECT 1 FROM work_wakeups x WHERE x.work_id=w.work_id AND x.state='pending' AND x.due_at<=%s)) "
                "SELECT w.* FROM eligible e JOIN works w USING(work_id,account_id) LEFT JOIN capacity_turns t "
                "ON t.account_id=w.account_id AND t.resource='coordination' AND t.lane='background' "
                "ORDER BY e.tenant_position,COALESCE(t.last_admitted_at,'-infinity'::timestamptz),w.work_id "
                "LIMIT %s FOR UPDATE OF w SKIP LOCKED",
                (now, limit),
            ).fetchall()
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
                requirement = WorkRepository.requirement(
                    uow, work["account_id"], work["work_id"], work["current_requirement_revision"]
                )
                wake = uow.execute(
                    "SELECT * FROM work_wakeups WHERE work_id=%s AND expected_revision=%s "
                    "AND state='pending' AND due_at<=%s ORDER BY due_at DESC,created_at DESC,wakeup_id DESC LIMIT 1 FOR UPDATE",
                    (work["work_id"], work["current_requirement_revision"], now),
                ).fetchone()
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
                        WorkRepository.event(uow, current, "advanced", run_id=run["run_id"])
                    count += 1
                except (ValueError, DomainError, RunBudgetExhausted) as exc:
                    logger.warning("Work admission blocked: %s", type(exc).__name__)
                    uow.execute(
                        "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1 WHERE work_id=%s",
                        (
                            json.dumps(
                                continuation(
                                    "blocked",
                                    "budget_exhausted"
                                    if isinstance(exc, RunBudgetExhausted)
                                    else "admission_rejected",
                                )
                            ),
                            work["work_id"],
                        ),
                    )
                    current = WorkRepository.get(uow, work["account_id"], work["work_id"])
                    WorkRepository.event(
                        uow, current, "blocked", reason=current["continuation"]["reason"]
                    )
                except Exception:
                    # The savepoint rolls back this admission only. Keep its wakeup
                    # pending for recovery and let other accounts make progress.
                    logger.exception("Work admission failed: work_id=%s", work["work_id"])
        return count
