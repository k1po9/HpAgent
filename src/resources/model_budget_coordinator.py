"""Atomic Account/day and per-Run model budget coordination."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from persistence.uow import UnitOfWork, retryable_transaction
from resources.account_daily_budget import (
    AccountBudgetMutation,
    AccountDailyBudgetService,
)
from resources.run_budget import BudgetMutation, RunBudgetService


@dataclass(frozen=True)
class CoordinatedBudgetMutation:
    account: AccountBudgetMutation
    run: BudgetMutation


class ModelBudgetCoordinator:
    """Runs both budget mutations in one short PostgreSQL transaction."""

    def __init__(self, database: object):
        self.database = database
        self.account_budget = AccountDailyBudgetService(database)
        self.run_budget = RunBudgetService(database)

    @retryable_transaction
    def reserve(
        self,
        account_id: UUID,
        run_id: UUID,
        operation_id: str,
        requested_total_tokens: int,
        run_amounts: Mapping[str, int],
        *,
        final_response: bool = False,
        at: datetime | None = None,
        snapshot_id: UUID | None = None,
        expected_entitlement_version: int | None = None,
        endpoint_access_tier: str | None = None,
    ) -> CoordinatedBudgetMutation:
        self._require_matching_total(requested_total_tokens, run_amounts)
        with UnitOfWork(self.database) as uow:
            account = self.account_budget.reserve_in_uow(
                uow,
                account_id,
                operation_id,
                requested_total_tokens,
                at=at,
                snapshot_id=snapshot_id,
                expected_entitlement_version=expected_entitlement_version,
                endpoint_access_tier=endpoint_access_tier,
            )
            run = self.run_budget.reserve_in_uow(
                uow,
                run_id,
                operation_id,
                run_amounts,
                final_response=final_response,
            )
            return CoordinatedBudgetMutation(account, run)

    @retryable_transaction
    def settle(
        self,
        account_id: UUID,
        run_id: UUID,
        operation_id: str,
        actual_total_tokens: int,
        run_actual: Mapping[str, int],
        usage_source: str,
        *,
        quota_date: date | None = None,
    ) -> CoordinatedBudgetMutation:
        self._require_matching_total(actual_total_tokens, run_actual)
        with UnitOfWork(self.database) as uow:
            account = self.account_budget.settle_in_uow(
                uow,
                account_id,
                operation_id,
                actual_total_tokens,
                usage_source,
                quota_date=quota_date,
            )
            run = self.run_budget.settle_in_uow(
                uow,
                run_id,
                operation_id,
                run_actual,
                usage_source,
            )
            return CoordinatedBudgetMutation(account, run)

    @retryable_transaction
    def release(
        self,
        account_id: UUID,
        run_id: UUID,
        operation_id: str,
        *,
        quota_date: date | None = None,
    ) -> CoordinatedBudgetMutation:
        with UnitOfWork(self.database) as uow:
            account = self.account_budget.release_in_uow(
                uow,
                account_id,
                operation_id,
                quota_date=quota_date,
            )
            run = self.run_budget.release_in_uow(uow, run_id, operation_id)
            return CoordinatedBudgetMutation(account, run)

    @staticmethod
    def _require_matching_total(total_tokens: int, run_values: Mapping[str, int]) -> None:
        if run_values.get("model_total_tokens") != total_tokens:
            raise ValueError("Account tokens must equal the Run model_total_tokens dimension")
