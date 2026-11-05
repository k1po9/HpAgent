"""Shared Chat commands; PG owns admission and atomic Message/Run/Outbox writes."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from uuid6 import uuid7

from application.chat.commands import RetryChatRunCommand, SendMessageCommand
from application.chat.commands import message_request_id as message_request_id
from application.chat.dependencies import ChatCommandDependencies
from application.chat.projection import ChatProjection
from application.chat.retry_run import RetryChatRun
from application.chat.send_message import SendMessage
from application.run.cancel_run import CancelRun
from application.run.commands import CancelRunCommand
from common.agent_policy import AgentPolicy
from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import (
    ResourceNotFound,
)

from .admission import AdmissionPolicy

logger = logging.getLogger("HpAgent.ConversationCommands")


def _id() -> UUID:
    """Generate application-owned UUIDv7 values; PostgreSQL needs no UUID extension."""
    return uuid7()


class CommandService:
    def __init__(
        self,
        database_url: object,
        *,
        budget_mode: str = "observe",
        budget_policy_version: str = "file-p0-v1",
        budget_limits: Mapping[str, int] | None = None,
        final_response_reserve_tokens: int = 1024,
        admission_policy: AdmissionPolicy | None = None,
        agent_policy: AgentPolicy | None = None,
    ):
        self.dependencies = ChatCommandDependencies(
            database_url,
            budget_mode=budget_mode,
            budget_policy_version=budget_policy_version,
            budget_limits=budget_limits,
            final_response_reserve_tokens=final_response_reserve_tokens,
            admission_policy=admission_policy,
            agent_policy=agent_policy,
        )
        self.send = SendMessage(self.dependencies)
        self.retry = RetryChatRun(self.dependencies)
        self.cancel = CancelRun(self.dependencies)

    @property
    def database_url(self):
        return self.dependencies.database_url

    @property
    def accounts(self):
        return self.dependencies.accounts

    @property
    def conversations(self):
        return self.dependencies.conversations

    @property
    def messages(self):
        return self.dependencies.messages

    @property
    def runs(self):
        return self.dependencies.runs

    @property
    def files(self):
        return self.dependencies.files

    @property
    def resource_policy(self):
        return self.dependencies.resource_policy

    @property
    def budgets(self):
        return self.dependencies.budgets

    @property
    def admission_policy(self):
        return self.dependencies.admission_policy

    @property
    def workflows(self):
        return self.dependencies.workflows

    @property
    def idempotency(self):
        return self.dependencies.idempotency

    @property
    def outbox(self):
        return self.dependencies.outbox

    @property
    def budget_mode(self):
        return self.dependencies.budget_mode

    @property
    def budget_policy_version(self):
        return self.dependencies.budget_policy_version

    @property
    def budget_limits(self):
        return self.dependencies.budget_limits

    @property
    def final_response_reserve_tokens(self):
        return self.dependencies.final_response_reserve_tokens

    @property
    def agent_policy(self):
        return self.dependencies.agent_policy

    @agent_policy.setter
    def agent_policy(self, value: AgentPolicy) -> None:
        self.dependencies.agent_policy = value

    @retryable_transaction
    def create_conversation(self, account_id: UUID, key: str, title: str = "") -> CommandResult:
        payload = {"title": title}
        with UnitOfWork(self.database_url) as uow:
            existing = self._claim(uow, account_id, "create_conversation", key, payload)
            if existing:
                return existing
            if not self.accounts.require_active(uow, account_id):
                raise ResourceNotFound()
            conversation_id = _id()
            self.conversations.create(uow, conversation_id, account_id, title)
            row = uow.execute(
                "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s",
                (account_id, conversation_id),
            ).fetchone()
            result = {
                "conversation_id": str(conversation_id),
                "conversation": ChatProjection.conversation_dto(row),
            }
            self._complete(uow, account_id, "create_conversation", key, 201, result)
            return CommandResult(201, result)

    def send_message(
        self,
        account_id: UUID,
        conversation_id: UUID,
        key: str,
        content: str,
        agent_strategy: str = "react",
        file_ids: tuple[UUID, ...] = (),
    ) -> CommandResult:
        return self.send.execute(
            SendMessageCommand(
                account_id,
                conversation_id,
                key,
                content,
                agent_strategy,
                file_ids,
            )
        )

    def _send_message_in_uow(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID,
        key: str,
        content: str,
        agent_strategy: str = "react",
        file_ids: tuple[UUID, ...] = (),
        *,
        fixed_html: dict[str, Any] | None = None,
    ) -> CommandResult:
        return self.send.execute_in_uow(
            uow,
            SendMessageCommand(
                account_id,
                conversation_id,
                key,
                content,
                agent_strategy,
                file_ids,
                fixed_html,
            ),
        )

    def start_run(self, account_id: UUID, run_id: UUID) -> bool:
        from run_domain.lifecycle import RunLifecycleService

        return cast(bool, RunLifecycleService(self.database_url).start(account_id, run_id))

    def complete_run(self, account_id: UUID, run_id: UUID, content: str) -> bool:
        from run_domain.lifecycle import RunLifecycleService

        return cast(
            bool,
            RunLifecycleService(self.database_url).finish(
                account_id, run_id, "succeeded", content=content
            ),
        )

    def fail_run(
        self, account_id: UUID, run_id: UUID, failure_code: str, failure_message: str | None = None
    ) -> bool:
        from run_domain.lifecycle import RunLifecycleService

        return cast(
            bool,
            RunLifecycleService(self.database_url).finish(
                account_id,
                run_id,
                "failed",
                failure_code=failure_code,
                failure_message=failure_message,
            ),
        )

    def cancelled_run(self, account_id: UUID, run_id: UUID) -> bool:
        from run_domain.lifecycle import RunLifecycleService

        return cast(
            bool, RunLifecycleService(self.database_url).finish(account_id, run_id, "cancelled")
        )

    def _claim(
        self, uow: UnitOfWork, account_id: UUID, operation: str, key: str, payload: object
    ) -> CommandResult | None:
        return self.dependencies.claim(uow, account_id, operation, key, payload)

    def _complete(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        response_status: int,
        result: dict[str, Any],
    ) -> None:
        self.dependencies.complete(uow, account_id, operation, key, response_status, result)

    def retry_run(self, account_id: UUID, source_run_id: UUID, key: str) -> CommandResult:
        return self.retry.execute(RetryChatRunCommand(account_id, source_run_id, key))

    def cancel_run(self, account_id: UUID, run_id: UUID, key: str) -> CommandResult:
        return self.cancel.execute(CancelRunCommand(account_id, run_id, key))

    def _cancel_run_in_uow(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        run_id: UUID,
        key: str,
    ) -> CommandResult:
        # Compatibility for existing Workspace transaction callers until their import migration.
        return self.cancel.execute_in_uow(uow, CancelRunCommand(account_id, run_id, key))
