"""One command boundary shared by API and application tools."""

from __future__ import annotations

import json
from typing import cast
from uuid import UUID

from uuid6 import uuid7

from application.work.accept_work import AcceptWork
from application.work.advance_work import AdvanceWork
from application.work.commands import AcceptWorkCommand, AdvanceWorkCommand
from application.work.dependencies import WorkCommandDependencies
from application.work.events import record_work_event
from common.agent_policy import AgentPolicy
from persistence.runs import RunRepository
from persistence.uow import UnitOfWork, retryable_transaction
from persistence.wakeups import WakeupRepository
from persistence.work_requirements import WorkRequirementRepository
from persistence.works import WorkRepository as WorkRows
from work_domain.errors import WorkConflict as WorkConflict
from work_domain.models import Requirement, continuation, validate_new_completion_criteria
from work_domain.save_policy import (
    defer_workspace_save,
    validate_new_save_policy,
)
from work_domain.time_policy import validate_new_timing


class WorkCommandService:
    def __init__(
        self,
        database: object,
        *,
        budget_mode: str = "enforce",
        agent_policy: AgentPolicy | None = None,
    ):
        self.dependencies = WorkCommandDependencies(
            database, budget_mode=budget_mode, agent_policy=agent_policy
        )
        self.accept_work = AcceptWork(self.dependencies)
        self.advance_work = AdvanceWork(self.dependencies)

    @property
    def database(self):
        return self.dependencies.database

    @property
    def budget_mode(self):
        return self.dependencies.budget_mode

    @property
    def agent_policy(self):
        return self.dependencies.agent_policy

    @agent_policy.setter
    def agent_policy(self, value: AgentPolicy) -> None:
        self.dependencies.agent_policy = value

    @property
    def repo(self):
        return self.dependencies.repo

    @property
    def idempotency(self):
        return self.dependencies.idempotency

    def accept(
        self,
        account_id: UUID,
        key: str,
        title: str,
        requirement: Requirement,
        *,
        conversation_id: UUID | None = None,
        source_message_id: UUID | None = None,
        workspace_save_requested: bool | None = None,
    ):
        return self.accept_work.execute(
            AcceptWorkCommand(
                account_id,
                key,
                title,
                requirement,
                conversation_id,
                source_message_id,
                workspace_save_requested,
            )
        )

    def _claim(self, uow, account_id, operation, key, payload):
        return self.dependencies.claim(uow, account_id, operation, key, payload)

    def _complete(self, uow, account_id, operation, key, work_id, status=200, **extra):
        return self.dependencies.complete(uow, account_id, operation, key, work_id, status, **extra)

    def _snapshot(self, uow, account_id, work_id):
        return self.dependencies.queries.snapshot_in_uow(uow, account_id, work_id)

    def get(self, account_id: UUID, work_id: UUID) -> dict:
        return cast(dict, self.dependencies.queries.get(account_id, work_id))

    def list(self, account_id: UUID, before: UUID | None = None) -> dict:
        return cast(dict, self.dependencies.queries.list(account_id, before))

    def records(self, account_id, work_id, kind, after=0):
        return self.dependencies.queries.records(account_id, work_id, kind, after)

    @staticmethod
    def _link(uow, work, conversation_id, command, source_message_id=None):
        return WorkCommandDependencies.link(uow, work, conversation_id, command, source_message_id)

    def _locked(self, uow, account_id, work_id, row_version):
        return self.dependencies.locked(uow, account_id, work_id, row_version)

    @staticmethod
    def _cancel_coordinator(uow, work, command_id=None, action=None):
        from delivery.service import cancel_pending

        cancel_pending(uow, work)
        run_id = work["active_coordinator_run_id"]
        if run_id:
            if command_id is not None:
                uow.execute(
                    "INSERT INTO execution_control_events(account_id,run_id,execution_id,command_id,action) "
                    "SELECT account_id,run_id,execution_id,%s,%s FROM run_executions WHERE run_id=%s "
                    "ON CONFLICT DO NOTHING",
                    (command_id, action, run_id),
                )
            run = uow.execute(
                "SELECT * FROM runs WHERE account_id=%s AND run_id=%s FOR UPDATE",
                (work["account_id"], run_id),
            ).fetchone()
            if run["status"] in {"queued", "running"}:
                RunRepository().request_coordinator_cancellation(uow, run_id)
            from persistence.outbox import OutboxRepository

            OutboxRepository().enqueue(
                uow,
                uuid7(),
                work["account_id"],
                "cancel_run",
                f"cancel-run:{run_id}",
                None,
                run_id,
                json.dumps({"run_id": str(run_id), "version": 1}),
            )
        WakeupRepository.supersede_pending(uow, work["work_id"])

    @retryable_transaction
    def revise(
        self,
        account_id,
        work_id,
        key,
        row_version,
        requirement: Requirement,
        reason="user_revision",
        *,
        workspace_save_requested: bool | None = None,
    ):
        value = requirement.to_dict()
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(
                uow,
                account_id,
                "revise_work",
                key,
                {
                    "work_id": str(work_id),
                    "row_version": row_version,
                    "requirement": value,
                    "reason": reason,
                },
            )
            if replay:
                return replay
            validate_new_completion_criteria(value)
            validate_new_save_policy(value, workspace_save_requested=workspace_save_requested)
            work = self._locked(uow, account_id, work_id, row_version)
            if work["status"] not in {"active", "pausing", "paused"}:
                raise WorkConflict(work, "work_not_revisable")
            previous = WorkRequirementRepository.get(
                uow, account_id, work_id, work["current_requirement_revision"]
            )
            if value["timing"] != previous["timing"]:
                validate_new_timing(value["timing"])
            revision = work["current_requirement_revision"] + 1
            WorkRequirementRepository.insert(
                uow, account_id, work_id, revision, value, command, reason
            )
            self._cancel_coordinator(uow, work, command, "revise")
            checkpoint = {
                **work["checkpoint"],
                "requirement_revision": revision,
                "needs_review": True,
                "checkpoint_version": work["checkpoint"]["checkpoint_version"] + 1,
            }
            from work_domain.timing import initial_continuation

            next_step = initial_continuation(value["timing"])
            if (
                work["continuation"].get("operation_ref")
                or work["continuation"]["reason"] == "budget_exhausted"
            ):
                next_step = work["continuation"]
            work = dict(
                WorkRows.revise(
                    uow, revision, json.dumps(checkpoint), json.dumps(next_step), work_id
                )
            )
            record_work_event(uow, work, "revised", command_id=command, reason=reason)
            if (
                not work["continuation"].get("operation_ref")
                and work["continuation"]["reason"] != "budget_exhausted"
            ):
                defer_workspace_save(uow, work, value)
                work = self.repo.get(uow, account_id, work_id)
            from application.work.schedules import sync_schedule

            sync_schedule(uow, work, value)
            if (
                work["status"] == "active"
                and work["continuation"]["kind"] != "awaiting_input"
                and value["timing"]["kind"] in {"immediate", "once"}
            ):
                WakeupRepository.enqueue(
                    uow,
                    work,
                    f"revision:{command}",
                    "revision",
                    "reevaluate_after_convergence",
                    next_step.get("due_at"),
                )
            return self._complete(uow, account_id, "revise_work", key, work_id)

    @retryable_transaction
    def control(self, account_id, work_id, key, row_version, action):
        if action not in {"pause", "resume", "stop"}:
            raise ValueError("unsupported control")
        operation = f"{action}_work"
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(
                uow,
                account_id,
                operation,
                key,
                {"work_id": str(work_id), "row_version": row_version},
            )
            if replay:
                return replay
            work = self._locked(uow, account_id, work_id, row_version)
            allowed = {
                "pause": {"active"},
                "resume": {"paused"},
                "stop": {"active", "pausing", "paused"},
            }
            if work["status"] not in allowed[action]:
                raise WorkConflict(work, "invalid_work_transition")
            from run_domain.lifecycle import RunLifecycleService

            effect = RunLifecycleService.unresolved_effect(uow, work)
            unresolved = bool(
                work["active_coordinator_run_id"]
                or work["continuation"].get("operation_ref")
                or effect
            )
            if effect:
                work["continuation"] = continuation(
                    "blocked", "side_effect_uncertain", operation_ref=effect["operation_id"]
                )
            status = {
                "pause": "pausing" if unresolved else "paused",
                "stop": "stopping" if unresolved else "stopped",
                "resume": "active",
            }[action]
            self._cancel_coordinator(uow, work, command, action if action != "resume" else None)
            work = dict(
                WorkRows.set_control_state(
                    uow, status, json.dumps(work["continuation"]), status, work_id
                )
            )
            event = {
                "pausing": "pause_requested",
                "paused": "paused",
                "stopping": "stop_requested",
                "stopped": "stopped",
                "active": "resumed",
            }[status]
            record_work_event(uow, work, event, command_id=command, status=status)
            requirement = WorkRequirementRepository.get(
                uow, account_id, work_id, work["current_requirement_revision"]
            )
            from application.work.schedules import sync_schedule

            if (
                action == "resume"
                and not work["continuation"].get("operation_ref")
                and work["continuation"]["reason"] != "budget_exhausted"
            ):
                from work_domain.progress import reset

                reset(uow, work)
                from work_domain.timing import initial_continuation

                next_step = initial_continuation(requirement["timing"])
                work = dict(
                    WorkRows.set_continuation_returning(uow, json.dumps(next_step), work_id)
                )
                defer_workspace_save(uow, work, requirement)
                work = self.repo.get(uow, account_id, work_id)
            sync_schedule(uow, work, requirement)
            if (
                action == "resume"
                and work["continuation"]["kind"] != "awaiting_input"
                and requirement["timing"]["kind"] in {"immediate", "once"}
            ):
                WakeupRepository.enqueue(
                    uow,
                    work,
                    f"resume:{command}",
                    "resume",
                    "explicit_resume",
                    work["continuation"].get("due_at"),
                )
            return self._complete(uow, account_id, operation, key, work_id)

    @retryable_transaction
    def link(self, account_id, work_id, conversation_id, key, row_version):
        with UnitOfWork(self.database) as uow:
            replay, command = self._claim(
                uow,
                account_id,
                "link_work",
                key,
                {
                    "work_id": str(work_id),
                    "conversation_id": str(conversation_id),
                    "row_version": row_version,
                },
            )
            if replay:
                return replay
            work = self._locked(uow, account_id, work_id, row_version)
            self._link(uow, work, conversation_id, command)
            if work["status"] not in {"stopped", "completed"}:
                work = dict(WorkRows.touch_returning(uow, work_id))
            record_work_event(
                uow, work, "linked", command_id=command, conversation_id=conversation_id
            )
            return self._complete(uow, account_id, "link_work", key, work_id)

    def integration(self, account_id, work_id, key, row_version, action, payload):
        from work_domain.integration import integration_command

        return integration_command(self, account_id, work_id, key, row_version, action, payload)

    def advance(self, account_id, work_id, key, row_version):
        return self.advance_work.execute(AdvanceWorkCommand(account_id, work_id, key, row_version))
