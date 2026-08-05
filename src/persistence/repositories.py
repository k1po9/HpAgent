"""Ownership-scoped repositories.  No method accepts a bare public resource ID."""
from __future__ import annotations

from typing import Any, cast
from uuid import UUID

from .uow import UnitOfWork


class ConversationRepository:
    def get_for_account(self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s" + suffix,
            (account_id, conversation_id),
        ).fetchone())


class RunRepository:
    def get_for_account(self, uow: UnitOfWork, account_id: UUID, run_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT * FROM runs WHERE account_id=%s AND run_id=%s" + suffix,
            (account_id, run_id),
        ).fetchone())

    def context_messages(self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, watermark: int) -> list[dict[str, Any]]:
        return list(uow.execute(
            "SELECT message_id,role,status,content,sequence FROM messages WHERE account_id=%s AND conversation_id=%s AND sequence<=%s AND ((role='user' AND status='accepted') OR (role='assistant' AND status='completed')) ORDER BY sequence",
            (account_id, conversation_id, watermark),
        ).fetchall())
