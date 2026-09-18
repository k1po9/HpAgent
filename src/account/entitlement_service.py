"""Read-only Account model-access entitlement projection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from uuid import UUID

from persistence.uow import UnitOfWork


class EntitlementState(str, Enum):
    ACCOUNT_UNAVAILABLE = "account_unavailable"
    MISSING = "missing"
    EXPIRED = "expired"
    VALID = "valid"


@dataclass(frozen=True)
class AccountEntitlement:
    account_id: UUID
    model_access_tier: str
    daily_token_limit: int | None
    prompt_visibility: str
    expires_at: datetime | None
    version: int
    provisioned_by_invite_id: UUID | None


@dataclass(frozen=True)
class EntitlementLookup:
    state: EntitlementState
    entitlement: AccountEntitlement | None = None


class EntitlementService:
    def __init__(self, database: object):
        self._database = database

    def get(self, account_id: UUID) -> EntitlementLookup:
        with UnitOfWork(self._database) as uow:
            return self.get_in_uow(uow, account_id)

    def get_in_uow(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        *,
        now: datetime | None = None,
    ) -> EntitlementLookup:
        """Resolve access inside a caller-owned transaction."""
        row = uow.execute(
            "SELECT a.status,e.model_access_tier,e.daily_token_limit,e.prompt_visibility,"
            "e.expires_at,e.version,e.provisioned_by_invite_id "
            "FROM accounts a LEFT JOIN account_entitlements e USING(account_id) "
            "WHERE a.account_id=%s",
            (account_id,),
        ).fetchone()
        if not row or row["status"] != "active":
            return EntitlementLookup(EntitlementState.ACCOUNT_UNAVAILABLE)
        if row["model_access_tier"] is None:
            return EntitlementLookup(EntitlementState.MISSING)
        entitlement = AccountEntitlement(
            account_id,
            row["model_access_tier"],
            row["daily_token_limit"],
            row["prompt_visibility"],
            row["expires_at"],
            row["version"],
            row["provisioned_by_invite_id"],
        )
        if entitlement.expires_at is not None and entitlement.expires_at <= (
            now or datetime.now(UTC)
        ):
            return EntitlementLookup(EntitlementState.EXPIRED, entitlement)
        return EntitlementLookup(EntitlementState.VALID, entitlement)
