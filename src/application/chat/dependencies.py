"""Concrete Chat repositories, effective configuration and transaction bookkeeping."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from uuid6 import uuid7

from common.agent_policy import AgentPolicy
from conversation_domain.admission import AdmissionPolicy, SingleActiveRunAdmission
from persistence.command_result import CommandResult
from persistence.repositories import (
    AccountRepository,
    ConversationRepository,
    FileRepository,
    IdempotencyRepository,
    MessageRepository,
    OutboxRepository,
    RunBudgetRepository,
    RunRepository,
    WorkflowExecutionRepository,
)
from persistence.uow import UnitOfWork
from web_domain.errors import (
    ConversationBusy,
    IdempotencyConflict,
)
from workspace.resources import ResourcePolicy


def _id() -> UUID:
    """Generate application-owned UUIDv7 values; PostgreSQL needs no UUID extension."""
    return uuid7()


def _digest(value: object) -> bytes:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).digest()


class ChatCommandDependencies:
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
        self.agent_policy = agent_policy or AgentPolicy()
        self.database_url = database_url
        self.accounts = AccountRepository()
        self.conversations = ConversationRepository()
        self.messages = MessageRepository()
        self.runs = RunRepository()
        self.files = FileRepository()
        self.resource_policy = ResourcePolicy(database_url)
        self.budgets = RunBudgetRepository()
        self.admission_policy = admission_policy or SingleActiveRunAdmission()
        self.workflows = WorkflowExecutionRepository()
        self.idempotency = IdempotencyRepository()
        self.outbox = OutboxRepository()
        self.budget_mode = budget_mode
        self.budget_policy_version = budget_policy_version
        self.budget_limits = dict(
            budget_limits
            or {
                "model_input_tokens": 80000,
                "model_output_tokens": 20000,
                "model_total_tokens": 100000,
                "model_calls": 20,
                "tool_calls": 20,
                "bytes_scanned": 512 * 1024 * 1024,
                "bytes_returned_to_model": 2 * 1024 * 1024,
                "bytes_written": 512 * 1024 * 1024,
                "output_file_bytes": 128 * 1024 * 1024,
                "wall_time_ms": 600000,
                "sources_discovered": 30,
                "source_fetches": 20,
                "research_iterations": 3,
            }
        )
        self.final_response_reserve_tokens = final_response_reserve_tokens

    def claim(
        self, uow: UnitOfWork, account_id: UUID, operation: str, key: str, payload: object
    ) -> CommandResult | None:
        digest = _digest(payload)
        row = self.idempotency.claim(
            uow,
            _id(),
            account_id,
            operation,
            key,
            digest,
            datetime.now(UTC) + timedelta(days=7),
        )
        if row is None:
            return None
        if row is None or bytes(row["request_hash"]) != digest:
            raise IdempotencyConflict()
        if row["status"] == "completed":
            return CommandResult(
                int(row["response_status"]),
                cast(dict[str, Any], row["response_body"]),
                replayed=True,
                resource_reused=(operation == "retry_run" and int(row["response_status"]) == 200),
            )
        raise ConversationBusy()

    def complete(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        response_status: int,
        result: dict[str, Any],
    ) -> None:
        self.idempotency.complete(
            uow, account_id, operation, key, response_status, json.dumps(result)
        )

    def enqueue_run_event(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID | None,
        run_id: UUID,
        event_type: str,
        terminal_status: str | None = None,
    ) -> None:
        key = (
            f"terminal:{run_id}:{terminal_status}"
            if event_type == "publish_terminal_event"
            else f"{event_type.replace('_', '-')}:{run_id}"
        )
        event_id = _id()
        payload = {"run_id": str(run_id), "version": 1}
        if terminal_status:
            payload["terminal_status"] = terminal_status
            payload["terminal_event_id"] = str(event_id)
        self.outbox.enqueue(
            uow,
            event_id,
            account_id,
            event_type,
            key,
            conversation_id,
            run_id,
            json.dumps(payload),
        )

    def create_budget(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        account_id: UUID,
        conversation_id: UUID,
    ) -> None:
        self.budgets.create_snapshot(
            uow,
            run_id,
            account_id,
            conversation_id,
            self.budget_policy_version,
            self.budget_mode,
            json.dumps(self.budget_limits, sort_keys=True),
            self.final_response_reserve_tokens,
        )
