"""One command boundary shared by API and application tools."""

from __future__ import annotations

import json
from typing import cast

from uuid6 import uuid7

from application.work.events import record_work_event
from common.runtime_config import CapacityConfig
from persistence.accounts import AccountRepository
from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from persistence.wakeups import WakeupRepository
from persistence.work_requirements import WorkRequirementRepository
from persistence.works import WorkRepository as WorkRows
from web_domain.errors import ResourceNotFound
from work_domain.errors import WorkConflict
from work_domain.models import validate_new_completion_criteria
from work_domain.save_policy import (
    defer_workspace_save,
    validate_new_save_policy,
)
from work_domain.serialization import dto
from work_domain.time_policy import validate_new_timing

from .commands import AcceptWorkCommand
from .dependencies import WorkCommandDependencies


class AcceptWork:
    def __init__(self, dependencies: WorkCommandDependencies):
        self.dependencies = dependencies

    @staticmethod
    def _prepare(command: AcceptWorkCommand):
        value = command.requirement.to_dict()
        title = command.title.strip()
        if not 1 <= len(title) <= 200:
            raise ValueError("invalid title")
        payload = {
            "title": title,
            "requirement": value,
            "conversation_id": dto(command.conversation_id),
            "source_message_id": dto(command.source_message_id),
        }
        return value, title, payload

    @retryable_transaction
    def execute(self, command: AcceptWorkCommand) -> CommandResult:
        prepared = self._prepare(command)
        with UnitOfWork(self.dependencies.database) as uow:
            return self._accept_in_uow(uow, command, prepared)

    def execute_in_uow(self, uow: UnitOfWork, command: AcceptWorkCommand) -> CommandResult:
        """Borrow the outer transaction; no connection, commit or command retry."""
        return self._accept_in_uow(uow, command, self._prepare(command))

    def _accept_in_uow(
        self, uow: UnitOfWork, command: AcceptWorkCommand, prepared
    ) -> CommandResult:
        value, title, payload = prepared
        account_id, key = command.account_id, command.key
        conversation_id, source_message_id = command.conversation_id, command.source_message_id
        workspace_save_requested = command.workspace_save_requested
        replay, command_id = self.dependencies.claim(uow, account_id, "accept_work", key, payload)
        if replay:
            return cast(CommandResult, replay)
        validate_new_completion_criteria(value)
        validate_new_save_policy(value, workspace_save_requested=workspace_save_requested)
        if not AccountRepository().require_active(uow, account_id):
            raise ResourceNotFound()
        validate_new_timing(value["timing"])
        source = (
            uow.execute(
                "SELECT conversation_id FROM messages WHERE account_id=%s AND message_id=%s "
                "AND (%s::uuid IS NULL OR conversation_id=%s)",
                (account_id, source_message_id, conversation_id, conversation_id),
            ).fetchone()
            if source_message_id
            else None
        )
        if source_message_id and source is None:
            raise ResourceNotFound()
        if source and conversation_id is None:
            conversation_id = source["conversation_id"]
        WorkRows.lock_accept_limit(uow, str(account_id))
        count = WorkRows.count_accepted(uow, account_id)["n"]
        if count >= CapacityConfig.from_environment().work_account_accept_limit:
            raise WorkConflict({}, "work_accept_limit")
        work_id = uuid7()
        from work_domain.timing import initial_continuation

        next_step = initial_continuation(value["timing"])
        WorkRows.insert(uow, work_id, account_id, title, json.dumps(next_step))
        from resources.work_budget import WorkBudgetService

        WorkBudgetService.create(uow, account_id, work_id)
        if (
            value["capability_key"] == "reminder"
            and value["spec"].get("target_ref") == "current_channel"
        ):
            origin = uow.execute(
                "SELECT origin FROM messages WHERE account_id=%s AND message_id=%s",
                (account_id, source_message_id),
            ).fetchone()
            if not origin or not origin["origin"]:
                raise ValueError("current channel requires a verified source message")
            from delivery.service import target_from_origin

            selected = target_from_origin(
                uow,
                account_id,
                work_id,
                origin["origin"],
                content_scope="summary",
                command_id=command_id,
            )
            value["spec"] = {**value["spec"], "target_ref": str(selected)}
        WorkRequirementRepository.insert(
            uow, account_id, work_id, 1, value, command_id, "accepted", source_message_id
        )
        work = self.dependencies.repo.get(uow, account_id, work_id)
        if conversation_id:
            self.dependencies.link(uow, work, conversation_id, command_id, source_message_id)
        record_work_event(uow, work, "accepted", command_id=command_id, status="active")
        from delivery.service import ensure_inbox

        ensure_inbox(uow, account_id, work_id)
        waiting_for_save = defer_workspace_save(uow, work, value)
        work = self.dependencies.repo.get(uow, account_id, work_id)
        from application.work.schedules import sync_schedule

        sync_schedule(uow, work, value)
        if not waiting_for_save and value["timing"]["kind"] in {"immediate", "once"}:
            WakeupRepository.enqueue(
                uow, work, f"accepted:{command_id}", "accepted", "accepted", next_step.get("due_at")
            )
        return self.dependencies.complete(uow, account_id, "accept_work", key, work_id, 201)
