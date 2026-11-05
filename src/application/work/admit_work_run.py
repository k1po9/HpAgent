"""Atomic Work admission in the caller transaction; Chat admission remains separate."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal
from uuid import UUID

from uuid6 import uuid7

from common.agent_policy import AgentPolicy
from persistence.repositories import OutboxRepository, RunBudgetRepository
from persistence.runs import RunRepository
from persistence.uow import UnitOfWork
from run_domain.creation import WorkRunInsert, build_work_budget_limits, build_work_input_snapshot
from work_domain.admission import (
    WorkAdmissionFacts,
    cumulative_budget_exhausted,
    decide_work_admission,
)
from work_domain.errors import WorkConflict
from work_domain.models import continuation
from work_domain.persistence import WorkRepository
from workspace.resources import ResourceDenied, ResourcePolicy


@dataclass(frozen=True)
class WorkAdmissionContext:
    work: dict
    requirement: dict
    wakeup_id: UUID | None
    caller: Literal["explicit", "scheduler", "legacy"] = "legacy"
    now: datetime | None = None

    def bind_in_uow(self, uow: UnitOfWork) -> WorkAdmissionContext:
        """Lock authoritative rows; supplied dictionaries are never proof of a lock."""
        work = WorkRepository.get(uow, self.work["account_id"], self.work["work_id"], lock=True)
        identity = (
            "current_requirement_revision",
            "control_epoch",
            "row_version",
            "active_coordinator_run_id",
        )
        if any(work[key] != self.work[key] for key in identity):
            raise WorkConflict(work, "work_admission_stale")
        if (
            self.requirement["account_id"],
            self.requirement["work_id"],
            self.requirement["revision"],
        ) != (work["account_id"], work["work_id"], work["current_requirement_revision"]):
            raise WorkConflict(work, "work_admission_stale")
        requirement = WorkRepository.requirement(
            uow, work["account_id"], work["work_id"], work["current_requirement_revision"]
        )
        if self.wakeup_id is not None:
            wake = uow.execute(
                "SELECT * FROM work_wakeups WHERE account_id=%s AND work_id=%s AND wakeup_id=%s FOR UPDATE",
                (work["account_id"], work["work_id"], self.wakeup_id),
            ).fetchone()
            if (
                not wake
                or wake["state"] != "pending"
                or wake["expected_revision"] != work["current_requirement_revision"]
            ):
                raise WorkConflict(work, "work_wakeup_unavailable")
        return replace(self, work=work, requirement=requirement)


@dataclass(frozen=True)
class WorkAdmissionResult:
    run: dict | None
    reason: str | None = None


class AdmitWorkRun:
    def __init__(self, database: object, budget_mode: str, agent_policy: AgentPolicy | None = None):
        self.database = database
        self.budget_mode = budget_mode
        self.agent_policy = agent_policy

    def execute_in_uow(self, uow: UnitOfWork, context: WorkAdmissionContext) -> WorkAdmissionResult:
        context = context.bind_in_uow(uow)
        work, requirement, wakeup_id = context.work, context.requirement, context.wakeup_id
        database, budget_mode, agent_policy = self.database, self.budget_mode, self.agent_policy
        account_id, work_id = work["account_id"], work["work_id"]
        from resources.capacity import CapacityService
        from resources.work_budget import WorkBudgetExhausted
        from work_domain.progress import blocked_reason

        # Applies equally to scheduler and explicit advance; a new Run cannot reset protection.
        facts = WorkAdmissionFacts(
            progress_block=blocked_reason(uow, work),
            budget_blocked=work["continuation"]["reason"] == "budget_exhausted",
        )
        decision = decide_work_admission(facts)
        if decision.outcome == "defer":
            return WorkAdmissionResult(None, decision.reason)
        from work_domain.save_policy import defer_workspace_save

        facts = replace(facts, save_ready=not defer_workspace_save(uow, work, requirement))
        decision = decide_work_admission(facts)
        if decision.outcome == "defer":
            return WorkAdmissionResult(None, decision.reason)
        facts = replace(facts, capacity_available=CapacityService.admission(uow, work))
        decision = decide_work_admission(facts)
        if decision.outcome == "defer":
            uow.execute(
                "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s",
                (json.dumps(continuation("ready", "waiting_capacity")), work_id),
            )
            return WorkAdmissionResult(None, decision.reason)
        budget = uow.execute(
            "SELECT limits,used,reserved FROM work_budgets WHERE account_id=%s AND work_id=%s",
            (account_id, work_id),
        ).fetchone()
        facts = replace(facts, cumulative_budget_exhausted=cumulative_budget_exhausted(budget))
        if decide_work_admission(facts).outcome == "reject":
            raise WorkBudgetExhausted("Work cumulative budget exhausted")
        if wakeup_id is None:
            raise ValueError("pending Work wakeup required")
        run_id = uuid7()
        epoch = work["control_epoch"] + 1
        from run_domain.strategy import WorkExecutionPlanner
        from work_domain.persistence import dto

        strategy_decision = WorkExecutionPlanner().plan(requirement)
        strategy, executor = strategy_decision.strategy_kind, strategy_decision.executor_key
        research = executor == "research_report"
        workflow_id = f"hpagent-web-run-{run_id}"
        agent_strategy = (
            requirement["spec"].get("reasoning_mode", "react")
            if strategy == "generic_agent"
            else None
        )
        snapshot = build_work_input_snapshot(work, requirement, wakeup_id, strategy, agent_policy)
        if requirement["timing"]["kind"] == "daily":
            from work_domain.progress import previous_daily

            baseline = previous_daily(uow, work)
            snapshot["daily_baseline_run_id"] = str(baseline["run_id"]) if baseline else None
        selected_artifacts = uow.execute(
            "SELECT a.artifact_version_id,a.role,f.ref_id AS result_ref_id FROM work_artifacts a "
            "LEFT JOIN capability_result_refs f ON f.account_id=a.account_id "
            "AND f.artifact_version_id=a.artifact_version_id AND f.type='artifact_version' "
            "WHERE a.account_id=%s AND a.work_id=%s AND a.source_requirement_revision=%s "
            "AND a.role IN ('input','evidence') ORDER BY a.created_at,f.ref_id LIMIT 100",
            (account_id, work_id, requirement["revision"]),
        ).fetchall()
        # Exact result metadata makes an explicitly selected old version discoverable.
        # It neither adopts it nor replaces current file/resource authorization.
        snapshot["artifact_refs"] = dto(
            [
                {key: value for key, value in row.items() if value is not None}
                for row in selected_artifacts
            ]
        )
        wakeup = uow.execute(
            "SELECT kind FROM work_wakeups WHERE wakeup_id=%s", (wakeup_id,)
        ).fetchone()
        previous = uow.execute(
            "SELECT run_id,status FROM runs WHERE account_id=%s AND work_id=%s "
            "AND requirement_revision=%s "
            "ORDER BY work_control_epoch DESC,run_id DESC LIMIT 1",
            (account_id, work_id, requirement["revision"]),
        ).fetchone()
        # The locked Work's monotonic epoch orders attempts even if the database
        # clock moves backwards. Only an explicit retry/advance has ancestry.
        # A later occurrence or a step following success is a new execution.
        retry = (
            previous
            if wakeup["kind"] in {"advance", "retry"}
            and previous
            and previous["status"] in {"failed", "cancelled"}
            else None
        )
        RunRepository().insert_work(
            uow,
            WorkRunInsert(
                run_id=run_id,
                account_id=account_id,
                work_id=work_id,
                requirement_revision=requirement["revision"],
                work_control_epoch=epoch,
                wakeup_id=wakeup_id,
                strategy_kind=strategy,
                executor_key=executor,
                workflow_id=workflow_id,
                agent_strategy=agent_strategy,
                retry_of_run_id=retry["run_id"] if retry else None,
                executor_version=strategy_decision.executor_version,
                strategy_policy_version=strategy_decision.strategy_policy_version,
                input_snapshot=dto(snapshot),
            ),
        )
        uow.execute(
            "UPDATE works SET active_coordinator_run_id=%s,control_epoch=%s,"
            "row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),continuation=%s::jsonb WHERE work_id=%s",
            (run_id, epoch, json.dumps(continuation("ready", "coordinator_admitted")), work_id),
        )
        uow.execute(
            "UPDATE work_wakeups SET state='consumed',run_id=%s WHERE wakeup_id=%s AND state='pending'",
            (run_id, wakeup_id),
        )
        uow.execute(
            "UPDATE work_wakeups SET state='superseded' WHERE work_id=%s AND "
            "state='pending' AND expected_revision<=%s",
            (work_id, requirement["revision"]),
        )
        limits = build_work_budget_limits(strategy, executor)
        RunBudgetRepository().create_snapshot(
            uow, run_id, account_id, None, "work-foundation-v1", budget_mode, json.dumps(limits), 0
        )
        policy = ResourcePolicy(database)
        policy.snapshot_in_uow(uow, account_id, run_id, work_id, "work")
        output = requirement["deliverable_policy"]
        if research and output.get("directory_id"):
            from uuid import UUID

            directory = UUID(output["directory_id"])
            operation = output.get("operation", "create_child")
            entry = UUID(output["entry_id"]) if output.get("entry_id") else None
            if operation not in {"create_child", "update_content"} or (
                operation == "update_content"
            ) != bool(entry):
                raise ValueError("invalid output operation")
            if not policy._grants(uow, account_id, "work", work_id, entry or directory, operation):
                raise ResourceDenied("Work output permission missing")
            expected_revision = expected_hash = None
            if entry:
                base = uow.execute(
                    "SELECT d.current_revision,d.current_sha256,d.current_file_id "
                    "FROM workspace_nodes n JOIN persistent_file_destinations d "
                    "ON d.account_id=n.account_id AND d.destination_id=n.destination_id "
                    "WHERE n.account_id=%s AND n.node_id=%s AND n.parent_id=%s "
                    "AND n.deleted_at IS NULL FOR SHARE OF d",
                    (account_id, entry, directory),
                ).fetchone()
                if not base or not policy._grants(
                    uow, account_id, "work", work_id, entry, "read_content"
                ):
                    raise ResourceDenied("Work update requires readable current version")
                expected_revision, expected_hash = base["current_revision"], base["current_sha256"]
                candidate = uow.execute(
                    "UPDATE run_resource_candidates SET fixed_file_id=%s,fixed_revision=%s,"
                    "fixed_at=now() WHERE run_id=%s AND node_id=%s RETURNING logical_name",
                    (base["current_file_id"], expected_revision, run_id, entry),
                ).fetchone()
                if candidate is None:
                    raise ResourceDenied("Work update target is outside fixed scope")
                grant = policy._grants(uow, account_id, "work", work_id, entry, "read_content")[0]
                uow.execute(
                    "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,node_id,"
                    "basis,grant_id) VALUES (%s,%s,%s,%s,%s,'workspace_grant',%s)",
                    (
                        uuid7(),
                        account_id,
                        run_id,
                        base["current_file_id"],
                        entry,
                        grant["grant_id"],
                    ),
                )
                uow.execute(
                    "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,"
                    "logical_name) VALUES (%s,NULL,%s,%s,'input',%s)",
                    (account_id, run_id, base["current_file_id"], candidate["logical_name"]),
                )
            uow.execute(
                "INSERT INTO research_run_save_intents(run_id,account_id,work_id,requirement_revision,"
                "target_directory_id,operation,output_kind,policy_version,required,operation_id,"
                "target_entry_id,expected_revision,expected_sha256) "
                "VALUES (%s,%s,%s,%s,%s,%s,'research_markdown',%s,%s,%s,%s,%s,%s)",
                (
                    run_id,
                    account_id,
                    work_id,
                    requirement["revision"],
                    directory,
                    operation,
                    requirement["revision"],
                    bool(output.get("required")),
                    f"workspace-save:{run_id}:research_markdown",
                    entry,
                    expected_revision,
                    expected_hash,
                ),
            )
        OutboxRepository().enqueue(
            uow,
            uuid7(),
            account_id,
            "start_run",
            f"start-run:{run_id}",
            None,
            run_id,
            json.dumps({"run_id": str(run_id), "version": 1}),
        )
        return WorkAdmissionResult(
            dict(uow.execute("SELECT * FROM runs WHERE run_id=%s", (run_id,)).fetchone())
        )
