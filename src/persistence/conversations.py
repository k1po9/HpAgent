"""Conversation rows and message-sequence SQL."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from .uow import UnitOfWork


class ConversationRepository:
    def get_for_account(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, *, lock: bool = False
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s" + suffix,
                (account_id, conversation_id),
            ).fetchone(),
        )

    def lock_active(self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID):
        return uow.execute(
            "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s "
            "AND status='active' FOR UPDATE",
            (account_id, conversation_id),
        ).fetchone()

    def allocate_messages(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, count: int
    ) -> int:
        return int(
            uow.execute(
                "UPDATE conversations SET last_message_seq=last_message_seq+%s,updated_at=now() "
                "WHERE account_id=%s AND conversation_id=%s RETURNING last_message_seq",
                (count, account_id, conversation_id),
            ).fetchone()["last_message_seq"]
        )

    def create(self, uow: UnitOfWork, conversation_id: UUID, account_id: UUID, title: str) -> None:
        uow.execute(
            "INSERT INTO conversations(conversation_id,account_id,title) VALUES (%s,%s,%s)",
            (conversation_id, account_id, title),
        )

    def ensure_surface(self, uow, account_id, conversation_id):
        uow.execute(
            "INSERT INTO conversations(account_id,conversation_id) VALUES (%s,%s) ON CONFLICT DO NOTHING",
            (account_id, conversation_id),
        )
