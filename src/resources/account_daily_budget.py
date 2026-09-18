"""UTC Account/day model-token reservation and settlement."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from account.entitlement_service import EntitlementService, EntitlementState
from persistence.uow import UnitOfWork, retryable_transaction
from resources.run_budget import USAGE_SOURCES


class AccountDailyBudgetError(RuntimeError):
    """Base class for stable Account quota protocol failures."""


class AccountModelEntitlementUnavailable(AccountDailyBudgetError):
    code = "account_model_entitlement_unavailable"


class AccountDailyBudgetExhausted(AccountDailyBudgetError):
    code = "account_daily_model_budget_exhausted"


class AccountDailyBudgetConflict(AccountDailyBudgetError):
    code = "account_daily_model_budget_operation_conflict"


@dataclass(frozen=True)
class AccountBudgetMutation:
    account_id: UUID
    quota_date: date
    operation_id: str
    tokens: int
    state: str
    replayed: bool = False


def _tokens(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("tokens must be a non-negative integer")
    return value


def _operation(value: str) -> str:
    if not value or len(value) > 200:
        raise ValueError("operation_id must contain 1 to 200 characters")
    return value


class AccountDailyBudgetService:
    def __init__(self, database: object):
        self.database = database
        self.entitlements = EntitlementService(database)

    @staticmethod
    def quota_date(at: datetime | None = None) -> date:
        instant = at or datetime.now(UTC)
        if instant.tzinfo is None:
            raise ValueError("quota clock must be timezone-aware")
        return instant.astimezone(UTC).date()

    @retryable_transaction
    def reserve(
        self, account_id: UUID, operation_id: str, tokens: int, *, at: datetime | None = None
    ) -> AccountBudgetMutation:
        with UnitOfWork(self.database) as uow:
            return self.reserve_in_uow(uow, account_id, operation_id, tokens, at=at)

    def reserve_in_uow(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation_id: str,
        tokens: int,
        *,
        at: datetime | None = None,
        snapshot_id: UUID | None = None,
    ) -> AccountBudgetMutation:
        requested = _tokens(tokens)
        operation_id = _operation(operation_id)
        lookup = self.entitlements.get_in_uow(uow, account_id, now=at)
        if lookup.state is not EntitlementState.VALID or lookup.entitlement is None:
            raise AccountModelEntitlementUnavailable(f"model entitlement is {lookup.state.value}")
        day = self.quota_date(at)
        uow.execute(
            "INSERT INTO account_daily_model_budgets(account_id,quota_date) VALUES (%s,%s) "
            "ON CONFLICT(account_id,quota_date) DO NOTHING",
            (account_id, day),
        )
        summary = uow.execute(
            "SELECT used_tokens,reserved_tokens FROM account_daily_model_budgets "
            "WHERE account_id=%s AND quota_date=%s FOR UPDATE",
            (account_id, day),
        ).fetchone()
        existing = uow.execute(
            "SELECT state,reserved_tokens,snapshot_id FROM account_model_usage_ledger "
            "WHERE account_id=%s AND quota_date=%s AND operation_id=%s",
            (account_id, day, operation_id),
        ).fetchone()
        if existing:
            if int(existing["reserved_tokens"]) != requested or existing["snapshot_id"] != snapshot_id:
                raise AccountDailyBudgetConflict("operation_id was reserved with different tokens")
            return AccountBudgetMutation(
                account_id, day, operation_id, requested, str(existing["state"]), True
            )
        limit = lookup.entitlement.daily_token_limit
        if (
            limit is not None
            and int(summary["used_tokens"]) + int(summary["reserved_tokens"]) + requested > limit
        ):
            raise AccountDailyBudgetExhausted("Account daily model token budget exhausted")
        uow.execute(
            "INSERT INTO account_model_usage_ledger(account_id,quota_date,operation_id,"
            "snapshot_id,state,reserved_tokens) VALUES (%s,%s,%s,%s,'reserved',%s)",
            (account_id, day, operation_id, snapshot_id, requested),
        )
        uow.execute(
            "UPDATE account_daily_model_budgets SET reserved_tokens=reserved_tokens+%s,updated_at=now() "
            "WHERE account_id=%s AND quota_date=%s",
            (requested, account_id, day),
        )
        return AccountBudgetMutation(account_id, day, operation_id, requested, "reserved")

    @retryable_transaction
    def settle(
        self,
        account_id: UUID,
        operation_id: str,
        actual_tokens: int,
        usage_source: str,
        *,
        quota_date: date | None = None,
    ) -> AccountBudgetMutation:
        with UnitOfWork(self.database) as uow:
            return self.settle_in_uow(
                uow, account_id, operation_id, actual_tokens, usage_source, quota_date=quota_date
            )

    def settle_in_uow(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation_id: str,
        actual_tokens: int,
        usage_source: str,
        *,
        quota_date: date | None = None,
    ) -> AccountBudgetMutation:
        actual = _tokens(actual_tokens)
        operation_id = _operation(operation_id)
        if usage_source not in USAGE_SOURCES:
            raise ValueError(f"unsupported usage source: {usage_source}")
        day, row = self._locked_ledger(uow, account_id, operation_id, quota_date)
        held = int(row["reserved_tokens"])
        if row["state"] == "settled":
            if int(row["actual_tokens"]) != actual or row["usage_source"] != usage_source:
                raise AccountDailyBudgetConflict("operation_id was already settled differently")
            return AccountBudgetMutation(account_id, day, operation_id, actual, "settled", True)
        if row["state"] != "reserved":
            raise AccountDailyBudgetConflict("operation is not reserved")
        uow.execute(
            "UPDATE account_model_usage_ledger SET state='settled',actual_tokens=%s,usage_source=%s,settled_at=now() WHERE account_id=%s AND quota_date=%s AND operation_id=%s",
            (actual, usage_source, account_id, day, operation_id),
        )
        uow.execute(
            "UPDATE account_daily_model_budgets SET reserved_tokens=reserved_tokens-%s,used_tokens=used_tokens+%s,updated_at=now() WHERE account_id=%s AND quota_date=%s",
            (held, actual, account_id, day),
        )
        return AccountBudgetMutation(account_id, day, operation_id, actual, "settled")

    @retryable_transaction
    def release(
        self, account_id: UUID, operation_id: str, *, quota_date: date | None = None
    ) -> AccountBudgetMutation:
        with UnitOfWork(self.database) as uow:
            return self.release_in_uow(uow, account_id, operation_id, quota_date=quota_date)

    def release_in_uow(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation_id: str,
        *,
        quota_date: date | None = None,
    ) -> AccountBudgetMutation:
        operation_id = _operation(operation_id)
        day, row = self._locked_ledger(uow, account_id, operation_id, quota_date)
        held = int(row["reserved_tokens"])
        if row["state"] == "released":
            return AccountBudgetMutation(account_id, day, operation_id, held, "released", True)
        if row["state"] != "reserved":
            raise AccountDailyBudgetConflict("settled usage cannot be released")
        uow.execute(
            "UPDATE account_model_usage_ledger SET state='released',settled_at=now() WHERE account_id=%s AND quota_date=%s AND operation_id=%s",
            (account_id, day, operation_id),
        )
        uow.execute(
            "UPDATE account_daily_model_budgets SET reserved_tokens=reserved_tokens-%s,updated_at=now() WHERE account_id=%s AND quota_date=%s",
            (held, account_id, day),
        )
        return AccountBudgetMutation(account_id, day, operation_id, held, "released")

    def _locked_ledger(
        self, uow: UnitOfWork, account_id: UUID, operation_id: str, quota_date: date | None
    ) -> tuple[date, object]:
        if quota_date is None:
            rows = uow.execute(
                "SELECT quota_date FROM account_model_usage_ledger "
                "WHERE account_id=%s AND operation_id=%s ORDER BY quota_date DESC",
                (account_id, operation_id),
            ).fetchall()
            if len(rows) != 1:
                raise AccountDailyBudgetConflict("operation is not uniquely reserved")
            day = rows[0]["quota_date"]
        else:
            day = quota_date
        summary = uow.execute(
            "SELECT 1 FROM account_daily_model_budgets "
            "WHERE account_id=%s AND quota_date=%s FOR UPDATE",
            (account_id, day),
        ).fetchone()
        row = uow.execute(
            "SELECT quota_date,state,reserved_tokens,actual_tokens,usage_source "
            "FROM account_model_usage_ledger WHERE account_id=%s AND quota_date=%s "
            "AND operation_id=%s FOR UPDATE",
            (account_id, day, operation_id),
        ).fetchone()
        if summary is None or row is None:
            raise AccountDailyBudgetConflict("operation is not reserved")
        return day, row
