"""RunBudgetRepository SQL operations in the caller-owned transaction."""

from __future__ import annotations

from uuid import UUID

from .uow import UnitOfWork


class RunBudgetRepository:
    def create_snapshot(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        account_id: UUID,
        conversation_id: UUID | None,
        policy_version: str,
        mode: str,
        limits: str,
        final_response_reserve_tokens: int,
    ) -> None:
        uow.execute(
            "INSERT INTO run_budgets(run_id,account_id,conversation_id,policy_version,mode,"
            "limits,final_response_reserve_tokens) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)",
            (
                run_id,
                account_id,
                conversation_id,
                policy_version,
                mode,
                limits,
                final_response_reserve_tokens,
            ),
        )
