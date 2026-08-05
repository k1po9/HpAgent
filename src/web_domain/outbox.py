from __future__ import annotations

import json
from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from uuid6 import uuid7

from persistence.repositories import (
    ConversationRepository,
    MessageRepository,
    OutboxRepository,
    RunRepository,
)
from persistence.uow import UnitOfWork, retryable_transaction

from .errors import OutboxLeaseLost, ResourceNotFound

OUTBOX_EVENT_TYPES = frozenset(
    {"start_run", "cancel_run", "retain_memory", "publish_terminal_event"}
)


def _safe_error(value: str, limit: int) -> str:
    return value.replace("\x00", "")[:limit]


class OutboxService:
    """Database-only Outbox lifecycle; external effects happen after claim commits."""

    def __init__(self, database_url: str):
        self.database_url = database_url
        self.repository = OutboxRepository()
        self.conversations = ConversationRepository()
        self.messages = MessageRepository()
        self.runs = RunRepository()

    @retryable_transaction
    def claim(
        self, worker_id: str, owned_event_types: Collection[str], limit: int = 10
    ):
        event_types = frozenset(owned_event_types)
        if not event_types or not event_types <= OUTBOX_EVENT_TYPES:
            raise ValueError("owned_event_types must contain known Outbox event types")
        with UnitOfWork(self.database_url) as uow:
            return self.repository.claim_batch(uow, worker_id, event_types, limit)

    @retryable_transaction
    def recover_expired(self, older_than_seconds: int) -> int:
        with UnitOfWork(self.database_url) as uow:
            return int(self.repository.recover_expired(uow, older_than_seconds))

    @retryable_transaction
    def mark_processed(self, event_id: UUID, worker_id: str) -> bool:
        with UnitOfWork(self.database_url) as uow:
            return bool(self.repository.mark_processed(uow, event_id, worker_id))

    @retryable_transaction
    def mark_retryable_failure(
        self, event_id: UUID, worker_id: str, error_code: str,
        safe_message: str, next_available_at: datetime
    ) -> bool:
        with UnitOfWork(self.database_url) as uow:
            return bool(self.repository.mark_retryable_failure(
                uow, event_id, worker_id, _safe_error(error_code, 100),
                _safe_error(safe_message, 1000), next_available_at
            ))

    @retryable_transaction
    def dead_letter(
        self, event_id: UUID, worker_id: str, error_code: str, safe_message: str
    ) -> bool:
        error_code = _safe_error(error_code, 100)
        safe_message = _safe_error(safe_message, 1000)
        with UnitOfWork(self.database_url) as uow:
            context = self.repository.discover_context(uow, event_id)
            if context is None:
                raise ResourceNotFound()
            self.conversations.get_for_account(
                uow, context["account_id"], context["conversation_id"], lock=True
            )
            run = self.runs.get_for_account(
                uow, context["account_id"], context["run_id"], lock=True
            )
            event = self.repository.lock_owned_event(uow, event_id, worker_id)
            if event is None or event["run_id"] != context["run_id"]:
                raise OutboxLeaseLost()
            if event["event_type"] == "start_run":
                if run and run["status"] == "queued":
                    self.messages.set_terminal(uow, run["run_id"], "failed")
                    self.runs.set_terminal(
                        uow, run["run_id"], "failed", "workflow_start_exhausted",
                        safe_message
                    )
                    terminal_event_id = uuid7()
                    self.repository.enqueue(
                        uow, terminal_event_id, run["account_id"], "publish_terminal_event",
                        f"terminal:{run['run_id']}:failed", run["conversation_id"],
                        run["run_id"], json.dumps({"run_id": str(run["run_id"]),
                                                   "terminal_status": "failed",
                                                   "terminal_event_id": str(terminal_event_id),
                                                   "version": 1}),
                    )
            changed = self.repository.mark_dead_letter(
                uow, event_id, worker_id, error_code, safe_message
            )
            if not changed:
                raise OutboxLeaseLost()
            return True
