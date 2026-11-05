"""Shared Chat commands; PG owns admission and atomic Message/Run/Outbox writes."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID

from uuid6 import uuid7

from agent_workflows.ids import tool_execution_workflow_id
from application.chat.commands import SendMessageCommand
from application.chat.commands import message_request_id as message_request_id
from application.chat.dependencies import ChatCommandDependencies
from application.chat.projection import ChatProjection
from application.chat.send_message import SendMessage
from common.agent_policy import AgentPolicy
from common.logging import log_event
from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import (
    FileNotReady,
    ResourceNotFound,
    RunNotCancellable,
    RunNotRetryable,
    RunRetryNotSafe,
)
from web_domain.failures import is_failure_retryable

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

    @retryable_transaction
    def cancel_run(self, account_id: UUID, run_id: UUID, key: str) -> CommandResult:
        with UnitOfWork(self.database_url) as uow:
            return self._cancel_run_in_uow(uow, account_id, run_id, key)

    def _cancel_run_in_uow(
        self, uow: UnitOfWork, account_id: UUID, run_id: UUID, key: str
    ) -> CommandResult:
        """Persist cancellation and, for dispatched Runs, its Outbox signal."""
        payload = {"run_id": str(run_id)}
        existing = self._claim(uow, account_id, "cancel_run", key, payload)
        if existing:
            return existing
        run = self._lock_owned_run(uow, account_id, run_id)
        if run["status"] in ("succeeded", "failed"):
            raise RunNotCancellable()
        if run["status"] != "cancelled":
            uow.execute(
                "INSERT INTO execution_control_events(account_id,run_id,execution_id,command_id,action) "
                "SELECT e.account_id,e.run_id,e.execution_id,c.idempotency_command_id,'cancel' "
                "FROM run_executions e JOIN idempotency_commands c ON c.account_id=e.account_id "
                "WHERE e.run_id=%s AND c.operation='cancel_run' AND c.idempotency_key=%s "
                "ON CONFLICT DO NOTHING",
                (run_id, key),
            )
        if run["status"] == "cancelled":
            result = ChatProjection.snapshot_for_run(uow, run_id)
            result.update({"run_id": str(run_id), "status": "cancelled"})
            response_status = 200
        elif run["status"] == "queued" and not self.workflows.has_current(uow, run_id):
            self._set_terminal(uow, account_id, run["conversation_id"], run_id, "cancelled")
            result = ChatProjection.snapshot_for_run(uow, run_id)
            result.update({"run_id": str(run_id), "status": "cancelled"})
            response_status = 200
        else:
            self.runs.set_cancelling(uow, run_id)
            pending_approvals = uow.execute(
                "UPDATE file_action_approvals SET status='cancelled',updated_at=now() "
                "WHERE account_id=%s AND run_id=%s AND status='pending' RETURNING *",
                (account_id, run_id),
            ).fetchall()
            for approval in pending_approvals:
                approval_event_id = _id()
                self.outbox.enqueue(
                    uow,
                    approval_event_id,
                    account_id,
                    "file_action_approval_decided",
                    f"file-approval:{approval['approval_id']}:cancelled",
                    run["conversation_id"],
                    run_id,
                    json.dumps(
                        {
                            "approval_id": str(approval["approval_id"]),
                            "operation_id": approval["operation_id"],
                            "tool_execution_workflow_id": tool_execution_workflow_id(
                                str(run_id), approval["operation_id"]
                            ),
                            "version": 1,
                        }
                    ),
                )
            self._outbox(uow, account_id, run["conversation_id"], run_id, "cancel_run")
            result = ChatProjection.snapshot_for_run(uow, run_id)
            result.update({"run_id": str(run_id), "status": "cancelling"})
            response_status = 202
        self._complete(uow, account_id, "cancel_run", key, response_status, result)
        return CommandResult(response_status, result)

    @retryable_transaction
    def retry_run(self, account_id: UUID, source_run_id: UUID, key: str) -> CommandResult:
        payload = {"run_id": str(source_run_id)}
        with UnitOfWork(self.database_url) as uow:
            existing = self._claim(uow, account_id, "retry_run", key, payload)
            if existing:
                return existing
            conversation_id = self.runs.discover_conversation(uow, account_id, source_run_id)
            if conversation_id is None:
                source = self.runs.get_for_account(uow, account_id, source_run_id)
                if source and source["source_kind"] == "work":
                    from work_domain.commands import WorkConflict

                    raise WorkConflict({"work_id": source["work_id"]}, "use_work_advance")
                raise ResourceNotFound()
            conversation = self.conversations.get_for_account(
                uow, account_id, conversation_id, lock=True
            )
            source = self.runs.get_for_account(uow, account_id, source_run_id, lock=True)
            if source is None:
                raise ResourceNotFound()
            if source["status"] != "failed":
                raise RunNotRetryable()
            failure_code = str(source.get("failure_code") or "")
            if not is_failure_retryable(failure_code):
                log_event(
                    logger,
                    logging.WARNING,
                    "run_retry_rejected",
                    "run",
                    run_id=str(source_run_id),
                    account_id=str(account_id),
                    failure_code=failure_code,
                    reason=RunRetryNotSafe(failure_code).reason,
                    status="rejected",
                )
                raise RunRetryNotSafe(failure_code)
            child = self.runs.direct_retry(uow, source_run_id)
            if child:
                result = ChatProjection.retry_result(uow, source_run_id, child["run_id"])
                response_status = 200
                resource_reused = True
            else:
                if conversation is None or conversation["status"] != "active":
                    raise ResourceNotFound()
                self.admission_policy.admit_in_locked_conversation(uow, source["conversation_id"])
                session_id = None
                sequence = self.conversations.allocate_messages(
                    uow, account_id, source["conversation_id"], 1
                )
                run_id = _id()
                assistant_message_id = _id()
                self.runs.insert(
                    uow,
                    run_id,
                    account_id,
                    source["conversation_id"],
                    session_id,
                    source["trigger_message_id"],
                    f"hpagent-web-run-{run_id}",
                    source["context_message_seq"],
                    source_run_id,
                    str(source.get("agent_strategy") or "react"),
                    fixed_html=source["input_snapshot"]
                    if source["executor_key"] == "artifact_html"
                    else None,
                    agent_policy=self.agent_policy.to_snapshot(),
                )
                self.messages.insert_assistant(
                    uow,
                    assistant_message_id,
                    account_id,
                    source["conversation_id"],
                    sequence,
                    run_id,
                )
                frozen_ids = [
                    row["file_id"]
                    for row in uow.execute(
                        "SELECT file_id FROM run_files WHERE run_id=%s AND direction='input'",
                        (source_run_id,),
                    ).fetchall()
                ]
                ready_count = uow.execute(
                    "SELECT count(*) AS count FROM stored_files WHERE account_id=%s "
                    "AND file_id=ANY(%s) AND status='ready'",
                    (account_id, frozen_ids),
                ).fetchone()["count"]
                if ready_count != len(frozen_ids):
                    raise FileNotReady()
                self.files.copy_retry_inputs(uow, source_run_id, run_id)
                self.resource_policy.copy_retry_in_uow(uow, source_run_id, run_id)
                self._create_budget(uow, run_id, account_id, source["conversation_id"])
                self._outbox(uow, account_id, source["conversation_id"], run_id, "start_run")
                result = ChatProjection.retry_result(uow, source_run_id, run_id)
                response_status = 202
                resource_reused = False
            self._complete(uow, account_id, "retry_run", key, response_status, result)
            return CommandResult(response_status, result, resource_reused=resource_reused)

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

    def _set_terminal(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID | None,
        run_id: UUID,
        status: str,
    ) -> None:
        from run_domain.lifecycle import RunLifecycleService

        run = self.runs.get_for_account(uow, account_id, run_id)
        RunLifecycleService(self.database_url).finish_in_uow(uow, run, status)

    def _lock_owned_run(self, uow: UnitOfWork, account_id: UUID, run_id: UUID) -> dict[str, Any]:
        from run_domain.lifecycle import RunLifecycleService

        return cast(dict[str, Any], RunLifecycleService.lock(uow, account_id, run_id))

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

    def _outbox(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID | None,
        run_id: UUID,
        event_type: str,
        terminal_status: str | None = None,
    ) -> None:
        self.dependencies.enqueue_run_event(
            uow, account_id, conversation_id, run_id, event_type, terminal_status
        )

    def _create_budget(
        self, uow: UnitOfWork, run_id: UUID, account_id: UUID, conversation_id: UUID
    ) -> None:
        self.dependencies.create_budget(uow, run_id, account_id, conversation_id)
