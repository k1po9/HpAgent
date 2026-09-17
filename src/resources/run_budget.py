"""Transactional Run budget reservation and usage settlement."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from persistence.uow import UnitOfWork, retryable_transaction

DIMENSIONS = frozenset({
    "model_input_tokens", "model_output_tokens", "model_total_tokens",
    "model_calls", "tool_calls", "bytes_scanned", "bytes_returned_to_model",
    "bytes_written", "output_file_bytes", "wall_time_ms",
    "sources_discovered", "source_fetches", "research_iterations",
})
USAGE_SOURCES = frozenset({"provider", "measured", "estimated"})


class RunBudgetError(RuntimeError):
    """Base class for stable budget protocol failures."""


class RunBudgetExhausted(RunBudgetError):
    code = "run_budget_exhausted"


class RunBudgetConflict(RunBudgetError):
    code = "run_budget_operation_conflict"


@dataclass(frozen=True)
class BudgetMutation:
    run_id: UUID
    operation_id: str
    values: dict[str, int]
    state: str
    replayed: bool = False


def _amounts(values: Mapping[str, int]) -> dict[str, int]:
    normalized: dict[str, int] = {}
    for dimension, raw in values.items():
        if dimension not in DIMENSIONS:
            raise ValueError(f"unsupported budget dimension: {dimension}")
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise ValueError(f"budget amount must be a non-negative integer: {dimension}")
        normalized[dimension] = raw
    if not normalized:
        raise ValueError("at least one budget dimension is required")
    return normalized


def _json_object(value: Any) -> dict[str, int]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, Mapping):
        raise RunBudgetError("budget JSON column is not an object")
    return {str(key): int(amount) for key, amount in value.items()}


class RunBudgetService:
    """Serializes mutations on ``run_budgets`` and deduplicates by operation.

    The service owns only short database transactions. External model/tool work
    must happen between ``reserve`` and ``settle``/``release`` calls.
    """

    def __init__(self, database: object):
        self.database = database

    @retryable_transaction
    def reserve(
        self,
        run_id: UUID,
        operation_id: str,
        amounts: Mapping[str, int],
        *,
        final_response: bool = False,
    ) -> BudgetMutation:
        requested = _amounts(amounts)
        if not operation_id or len(operation_id) > 200:
            raise ValueError("operation_id must contain 1 to 200 characters")
        with UnitOfWork(self.database) as uow:
            budget = uow.execute(
                "SELECT mode,status,limits,used,reserved,final_response_reserve_tokens "
                "FROM run_budgets WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if budget is None:
                raise RunBudgetError("Run budget snapshot not found")
            existing_rows = uow.execute(
                "SELECT dimension,state,reserved_amount FROM run_usage_ledger "
                "WHERE run_id=%s AND operation_id=%s",
                (run_id, operation_id),
            ).fetchall()
            if existing_rows:
                existing = {
                    str(row["dimension"]): int(row["reserved_amount"])
                    for row in existing_rows
                }
                if existing != requested:
                    raise RunBudgetConflict(
                        "operation_id was already reserved with different dimensions"
                    )
                return BudgetMutation(
                    run_id, operation_id, requested,
                    str(existing_rows[0]["state"]), replayed=True,
                )

            limits = _json_object(budget["limits"])
            used = _json_object(budget["used"])
            reserved = _json_object(budget["reserved"])
            reserve_for_final = int(budget["final_response_reserve_tokens"])
            exceeded: list[str] = []
            for dimension, amount in requested.items():
                limit = limits.get(dimension)
                if limit is None:
                    raise RunBudgetError(f"budget snapshot misses dimension: {dimension}")
                protected = (
                    reserve_for_final
                    if not final_response and dimension in {
                        "model_output_tokens", "model_total_tokens"
                    }
                    else 0
                )
                if used.get(dimension, 0) + reserved.get(dimension, 0) + amount > max(
                    0, limit - protected
                ):
                    exceeded.append(dimension)
            enforced_exhaustion = bool(exceeded and budget["mode"] == "enforce")
            if enforced_exhaustion:
                uow.execute(
                    "UPDATE run_budgets SET status='exhausted',updated_at=now() "
                    "WHERE run_id=%s", (run_id,),
                )
            else:
                for dimension, amount in requested.items():
                    uow.execute(
                        "INSERT INTO run_usage_ledger(run_id,operation_id,dimension,state,"
                        "reserved_amount) VALUES (%s,%s,%s,'reserved',%s)",
                        (run_id, operation_id, dimension, amount),
                    )
                    reserved[dimension] = reserved.get(dimension, 0) + amount
                status = "exhausted" if exceeded else str(budget["status"])
                uow.execute(
                    "UPDATE run_budgets SET reserved=%s::jsonb,status=%s,updated_at=now() "
                    "WHERE run_id=%s",
                    (json.dumps(reserved, sort_keys=True), status, run_id),
                )
        if enforced_exhaustion:
            raise RunBudgetExhausted(
                "Run budget exhausted for: " + ", ".join(sorted(exceeded))
            )
        return BudgetMutation(run_id, operation_id, requested, "reserved")

    def settle(
        self,
        run_id: UUID,
        operation_id: str,
        actual: Mapping[str, int],
        usage_source: str,
    ) -> BudgetMutation:
        if usage_source not in USAGE_SOURCES:
            raise ValueError(f"unsupported usage source: {usage_source}")
        return cast(
            BudgetMutation,
            self._finish(run_id, operation_id, _amounts(actual), usage_source),
        )

    @retryable_transaction
    def _finish(
        self, run_id: UUID, operation_id: str, actual: dict[str, int], source: str,
    ) -> BudgetMutation:
        with UnitOfWork(self.database) as uow:
            budget = uow.execute(
                "SELECT limits,used,reserved,status FROM run_budgets "
                "WHERE run_id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if budget is None:
                raise RunBudgetError("Run budget snapshot not found")
            rows = uow.execute(
                "SELECT dimension,state,reserved_amount,actual_amount,usage_source "
                "FROM run_usage_ledger WHERE run_id=%s AND operation_id=%s FOR UPDATE",
                (run_id, operation_id),
            ).fetchall()
            reserved_values = {str(row["dimension"]): int(row["reserved_amount"]) for row in rows}
            if reserved_values.keys() != actual.keys():
                raise RunBudgetConflict("settlement dimensions differ from reservation")
            if rows and all(row["state"] == "settled" for row in rows):
                previous = {str(row["dimension"]): int(row["actual_amount"]) for row in rows}
                if previous != actual or any(row["usage_source"] != source for row in rows):
                    raise RunBudgetConflict("operation_id was already settled differently")
                return BudgetMutation(run_id, operation_id, actual, "settled", replayed=True)
            if not rows or any(row["state"] != "reserved" for row in rows):
                raise RunBudgetConflict("operation is not reserved")
            used = _json_object(budget["used"])
            reserved = _json_object(budget["reserved"])
            limits = _json_object(budget["limits"])
            for dimension, amount in actual.items():
                held = reserved_values[dimension]
                reserved[dimension] = max(0, reserved.get(dimension, 0) - held)
                used[dimension] = used.get(dimension, 0) + amount
                uow.execute(
                    "UPDATE run_usage_ledger SET state='settled',actual_amount=%s,"
                    "usage_source=%s,settled_at=now() WHERE run_id=%s AND operation_id=%s "
                    "AND dimension=%s",
                    (amount, source, run_id, operation_id, dimension),
                )
            exhausted = any(
                used.get(dimension, 0) + reserved.get(dimension, 0) > limit
                for dimension, limit in limits.items()
            )
            status = "exhausted" if exhausted else str(budget["status"])
            uow.execute(
                "UPDATE run_budgets SET used=%s::jsonb,reserved=%s::jsonb,status=%s,"
                "updated_at=now() WHERE run_id=%s",
                (
                    json.dumps(used, sort_keys=True),
                    json.dumps(reserved, sort_keys=True),
                    status,
                    run_id,
                ),
            )
        return BudgetMutation(run_id, operation_id, actual, "settled")

    @retryable_transaction
    def release(self, run_id: UUID, operation_id: str) -> BudgetMutation:
        with UnitOfWork(self.database) as uow:
            budget = uow.execute(
                "SELECT reserved FROM run_budgets WHERE run_id=%s FOR UPDATE", (run_id,),
            ).fetchone()
            if budget is None:
                raise RunBudgetError("Run budget snapshot not found")
            rows = uow.execute(
                "SELECT dimension,state,reserved_amount FROM run_usage_ledger "
                "WHERE run_id=%s AND operation_id=%s FOR UPDATE",
                (run_id, operation_id),
            ).fetchall()
            if not rows:
                raise RunBudgetConflict("operation is not reserved")
            values = {str(row["dimension"]): int(row["reserved_amount"]) for row in rows}
            if all(row["state"] == "released" for row in rows):
                return BudgetMutation(run_id, operation_id, values, "released", replayed=True)
            if any(row["state"] != "reserved" for row in rows):
                raise RunBudgetConflict("settled usage cannot be released")
            reserved = _json_object(budget["reserved"])
            for dimension, amount in values.items():
                reserved[dimension] = max(0, reserved.get(dimension, 0) - amount)
            uow.execute(
                "UPDATE run_usage_ledger SET state='released',settled_at=now() "
                "WHERE run_id=%s AND operation_id=%s",
                (run_id, operation_id),
            )
            uow.execute(
                "UPDATE run_budgets SET reserved=%s::jsonb,updated_at=now() WHERE run_id=%s",
                (json.dumps(reserved, sort_keys=True), run_id),
            )
        return BudgetMutation(run_id, operation_id, values, "released")
