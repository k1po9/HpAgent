"""Atomic Account/day and per-Run model budget coordination."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

from persistence.uow import UnitOfWork, retryable_transaction
from resources.account_daily_budget import (
    AccountBudgetMutation,
    AccountDailyBudgetConflict,
    AccountDailyBudgetService,
)
from resources.run_budget import BudgetMutation, RunBudgetError, RunBudgetService


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

    def recover_terminal_in_uow(self, uow: UnitOfWork, run) -> None:
        """Close orphaned model holds in the same transaction as the Run terminal.

        A dispatch marker is conservative evidence of a possibly sent request,
        never a response receipt. Existing measured settlements stay untouched.
        The caller holds Run authority; no external I/O or new ledger is needed.
        """
        if run["status"] not in {"succeeded", "failed", "cancelled"}:
            raise ValueError("model hold recovery requires a terminal Run")
        rows = uow.execute(
            "SELECT l.operation_id,l.quota_date,l.reserved_tokens,"
            "EXISTS(SELECT 1 FROM model_dispatches d WHERE d.account_id=s.account_id "
            "AND d.snapshot_id=s.snapshot_id) AS dispatched "
            "FROM account_model_usage_ledger l JOIN model_input_snapshots s "
            "ON s.account_id=l.account_id AND s.snapshot_id=l.snapshot_id "
            "WHERE s.account_id=%s AND s.run_id=%s AND l.state='reserved' "
            "ORDER BY l.quota_date,l.operation_id",
            (run["account_id"], run["run_id"]),
        ).fetchall()
        for row in rows:
            operation = row["operation_id"]
            # Settlement may have completed since the candidate query. Serialize
            # with the existing Account/day -> ledger order before deciding.
            _, ledger = self.account_budget._locked_ledger(
                uow, run["account_id"], operation, row["quota_date"],
            )
            if ledger["state"] != "reserved":
                continue
            if row["dispatched"]:
                amounts = {
                    value["dimension"]: int(value["reserved_amount"])
                    for value in uow.execute(
                        "SELECT dimension,reserved_amount FROM run_usage_ledger "
                        "WHERE run_id=%s AND operation_id=%s",
                        (run["run_id"], operation),
                    ).fetchall()
                }
                self._require_matching_total(int(row["reserved_tokens"]), amounts)
                self.account_budget.settle_in_uow(
                    uow, run["account_id"], operation, int(row["reserved_tokens"]), "estimated",
                    quota_date=row["quota_date"],
                )
                self.run_budget.settle_in_uow(uow, run["run_id"], operation, amounts, "estimated")
            else:
                self.account_budget.release_in_uow(
                    uow, run["account_id"], operation, quota_date=row["quota_date"],
                )
                self.run_budget.release_in_uow(uow, run["run_id"], operation)

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
            from run_domain.execution import require_active_run

            if uow.execute(
                "SELECT 1 FROM run_budgets b JOIN runs r USING(run_id) "
                "WHERE r.account_id=%s AND r.run_id=%s", (account_id, run_id),
            ).fetchone() is None:
                raise RunBudgetError("Run budget snapshot not found")
            # Use the same authority -> Account/day -> Work/Run budget lock
            # order as terminal recovery, including callers without a fence.
            require_active_run(uow, str(account_id), str(run_id))
            prior_day = None
            if snapshot_id is not None:
                prior = uow.execute(
                    "SELECT quota_date FROM account_model_usage_ledger "
                    "WHERE account_id=%s AND operation_id=%s AND snapshot_id=%s "
                    "ORDER BY quota_date", (account_id, operation_id, snapshot_id),
                ).fetchall()
                if len(prior) > 1:
                    raise AccountDailyBudgetConflict("snapshot reservation has multiple quota dates")
                if prior:
                    prior_day = prior[0]["quota_date"]
            account = self.account_budget.reserve_in_uow(
                uow,
                account_id,
                operation_id,
                requested_total_tokens,
                at=at,
                snapshot_id=snapshot_id,
                expected_entitlement_version=expected_entitlement_version,
                endpoint_access_tier=endpoint_access_tier,
                quota_date=prior_day,
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
            # Serialize release with dispatch ownership on the original ledger.
            self.account_budget._locked_ledger(uow, account_id, operation_id, quota_date)
            if uow.execute(
                "SELECT 1 FROM model_input_snapshots s JOIN model_dispatches d "
                "USING(account_id,snapshot_id) WHERE s.account_id=%s AND s.run_id=%s "
                "AND s.operation_id=%s",
                (account_id, run_id, operation_id),
            ).fetchone():
                raise AccountDailyBudgetConflict("a dispatched model reservation cannot be released")
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
