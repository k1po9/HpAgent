"""Ownership-scoped repositories.  No method accepts a bare public resource ID."""

from __future__ import annotations

from uuid import UUID

from .uow import UnitOfWork


class AccountRepository:
    def is_active(self, uow: UnitOfWork, account_id: UUID) -> bool:
        return (
            uow.execute(
                "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'", (account_id,)
            ).fetchone()
            is not None
        )

    def require_active(self, uow: UnitOfWork, account_id: UUID) -> bool:
        return self.is_active(uow, account_id)
