"""Web Conversation session lifecycle.

This is deliberately separate from :mod:`session.store`, which remains the
QQ legacy Redis/WAL implementation.  A Web session is authoritative only in
the app-postgres ``sessions`` table and is always scoped by Conversation.
"""
from __future__ import annotations

from typing import cast
from uuid import UUID

from uuid6 import uuid7

from persistence.repositories import ConversationRepository, RunRepository, SessionRepository
from persistence.uow import UnitOfWork, retryable_transaction

from .errors import ConversationBusy, ResourceNotFound


class ConversationSessionService:
    """Create and rotate the one active Session of a Web Conversation.

    Methods which create a Session first lock the Conversation row.  No method
    reads Redis or accepts an account-level active-session pointer.
    """

    def __init__(self, database_url: object):
        self.database_url = database_url
        self.conversations = ConversationRepository()
        self.runs = RunRepository()
        self.sessions = SessionRepository()

    def get_or_create_in_locked_conversation(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID
    ) -> UUID:
        """Use only when the caller already holds the Conversation row lock."""
        return cast(
            UUID,
            self.sessions.get_or_create_active(uow, account_id, conversation_id, uuid7()),
        )

    @retryable_transaction
    def get_or_create_active(self, account_id: UUID, conversation_id: UUID) -> UUID:
        with UnitOfWork(self.database_url) as uow:
            if not self.conversations.lock_active(uow, account_id, conversation_id):
                raise ResourceNotFound()
            return self.get_or_create_in_locked_conversation(uow, account_id, conversation_id)

    @retryable_transaction
    def rotate_active(
        self, account_id: UUID, conversation_id: UUID, *, failed: bool = False
    ) -> UUID:
        """Create a successor only while the Conversation has no active Run."""
        with UnitOfWork(self.database_url) as uow:
            if not self.conversations.lock_active(uow, account_id, conversation_id):
                raise ResourceNotFound()
            if self.runs.has_active(uow, conversation_id):
                raise ConversationBusy()
            active = self.sessions.get_active(uow, account_id, conversation_id)
            if active is None:
                return cast(
                    UUID,
                    self.sessions.get_or_create_active(
                        uow, account_id, conversation_id, uuid7()
                    ),
                )
            status = "failed" if failed else "archived"
            self.sessions.transition_active(
                uow, account_id, conversation_id, active["session_id"], status
            )
            return cast(
                UUID,
                self.sessions.create_successor(
                    uow, account_id, conversation_id, active["session_id"], uuid7()
                ),
            )
