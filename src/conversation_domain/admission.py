"""Admission decisions use the caller's PG transaction and Conversation row lock.

The active-Run unique index backs the first policy. Queue/interrupt/append policies
must change the corresponding PG transitions and constraints together; they must
never introduce an in-memory or surface-owned admission authority.
"""
from typing import Protocol
from uuid import UUID

from persistence.repositories import RunRepository
from persistence.uow import UnitOfWork
from web_domain.errors import ConversationBusy


class AdmissionPolicy(Protocol):
    def admit_in_locked_conversation(
        self, uow: UnitOfWork, conversation_id: UUID,
    ) -> None:
        """Decide inside the command transaction, after ownership and row locking."""
        ...


class SingleActiveRunAdmission:
    """Initial policy: queued/running/cancelling Runs occupy one admission slot."""

    def admit_in_locked_conversation(
        self, uow: UnitOfWork, conversation_id: UUID,
    ) -> None:
        if RunRepository().has_active(uow, conversation_id):
            raise ConversationBusy()
