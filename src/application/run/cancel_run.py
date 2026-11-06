"""Cancel a Chat or Work attempt in an owned or caller-provided transaction."""

from __future__ import annotations

import json

from uuid6 import uuid7

from agent_workflows.ids import tool_execution_workflow_id
from application.chat.dependencies import ChatCommandDependencies
from application.chat.projection import ChatProjection
from persistence.command_result import CommandResult
from persistence.uow import UnitOfWork, retryable_transaction
from run_domain.lifecycle import RunLifecycleService
from web_domain.errors import (
    RunNotCancellable,
)

from .commands import CancelRunCommand


class CancelRun:
    def __init__(self, dependencies: ChatCommandDependencies):
        self.dependencies = dependencies

    @retryable_transaction
    def execute(self, command: CancelRunCommand) -> CommandResult:
        with UnitOfWork(self.dependencies.database) as uow:
            return self.execute_in_uow(uow, command)

    def execute_in_uow(self, uow: UnitOfWork, command: CancelRunCommand) -> CommandResult:
        """Borrow a transaction; never independently commit or retry it."""
        account_id, run_id, key = command.account_id, command.run_id, command.key
        payload = {"run_id": str(run_id)}
        existing = self.dependencies.claim(uow, account_id, "cancel_run", key, payload)
        if existing:
            return existing
        run = RunLifecycleService.lock(uow, account_id, run_id)
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
        elif run["status"] == "queued" and not self.dependencies.workflows.has_current(uow, run_id):
            terminal_run = self.dependencies.runs.get_for_account(uow, account_id, run_id)
            RunLifecycleService(self.dependencies.database).finish_in_uow(
                uow, terminal_run, "cancelled"
            )
            result = ChatProjection.snapshot_for_run(uow, run_id)
            result.update({"run_id": str(run_id), "status": "cancelled"})
            response_status = 200
        else:
            self.dependencies.runs.set_cancelling(uow, run_id)
            pending_approvals = uow.execute(
                "UPDATE file_action_approvals SET status='cancelled',updated_at=now() "
                "WHERE account_id=%s AND run_id=%s AND status='pending' RETURNING *",
                (account_id, run_id),
            ).fetchall()
            for approval in pending_approvals:
                approval_event_id = uuid7()
                self.dependencies.outbox.enqueue(
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
            self.dependencies.enqueue_run_event(
                uow, account_id, run["conversation_id"], run_id, "cancel_run"
            )
            result = ChatProjection.snapshot_for_run(uow, run_id)
            result.update({"run_id": str(run_id), "status": "cancelling"})
            response_status = 202
        self.dependencies.complete(uow, account_id, "cancel_run", key, response_status, result)
        return CommandResult(response_status, result)
