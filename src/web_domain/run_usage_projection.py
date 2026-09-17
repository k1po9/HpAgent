"""Authoritative Web projection of one Run's model usage budget."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork

MODEL_DIMENSIONS = (
    "model_input_tokens", "model_output_tokens", "model_total_tokens", "model_calls",
)


def budget_dto(
    budget_row: Mapping[str, Any] | None,
    usage_rows: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if budget_row is None:
        return None
    limits = budget_row["limits"]
    used = budget_row["used"]
    reserved = budget_row["reserved"]
    by_source = {
        source: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        for source in ("provider", "measured", "estimated")
    }
    calls = {"settled": 0, "in_flight": 0, "unmetered": 0}
    settled_sources: set[str] = set()
    token_keys = {
        "model_input_tokens": "input_tokens",
        "model_output_tokens": "output_tokens",
        "model_total_tokens": "total_tokens",
    }
    for row in usage_rows or ():
        state, dimension = str(row["state"]), str(row["dimension"])
        source = row.get("usage_source")
        count = int(row.get("operation_count") or 0)
        if dimension == "model_calls":
            key = {
                "settled": "settled", "reserved": "in_flight", "released": "unmetered",
            }.get(state)
            if key:
                calls[key] += count
        if state == "settled" and source in by_source and dimension in token_keys:
            by_source[source][token_keys[dimension]] += int(row.get("actual_sum") or 0)
            settled_sources.add(str(source))

    usage_state = (
        "partial" if calls["unmetered"] else
        "in_flight" if calls["in_flight"] else
        "complete" if calls["settled"] else "none"
    )
    token_sources = settled_sources & {"provider", "estimated"}
    usage_quality = (
        "provider" if token_sources == {"provider"} else
        "estimated" if token_sources == {"estimated"} else
        "mixed" if token_sources else "none"
    )

    def counter(dimension: str) -> dict[str, int]:
        return {
            "used": int(used.get(dimension, 0)),
            "reserved": int(reserved.get(dimension, 0)),
            "limit": int(limits.get(dimension, 0)),
        }

    return {
        "status": budget_row["status"], "mode": budget_row["mode"],
        "policy_version": budget_row["policy_version"],
        "tokens": {
            "input": counter("model_input_tokens"),
            "output": counter("model_output_tokens"),
            "total": counter("model_total_tokens"),
        },
        "model_calls": {
            **calls, "total_attempts": sum(calls.values()),
            "limit": int(limits.get("model_calls", 0)),
        },
        "by_source": by_source,
        "usage_state": usage_state,
        "usage_quality": usage_quality,
        "has_estimates": "estimated" in settled_sources,
        **{
            f"{dimension}_{suffix}": int(source.get(dimension, 0))
            for dimension in ("model_total_tokens", "tool_calls", "bytes_scanned")
            for suffix, source in (("used", used), ("limit", limits))
        },
    }


def load_run_budget_projection(
    uow: UnitOfWork, run_id: UUID | str,
) -> dict[str, Any] | None:
    budget = uow.execute(
        "SELECT * FROM run_budgets WHERE run_id=%s", (run_id,)
    ).fetchone()
    usage_rows = uow.execute(
        "SELECT state,usage_source,dimension,"
        "SUM(COALESCE(actual_amount,0)) AS actual_sum,"
        "COUNT(DISTINCT operation_id) AS operation_count "
        "FROM run_usage_ledger WHERE run_id=%s AND dimension = ANY(%s) "
        "GROUP BY state,usage_source,dimension",
        (run_id, list(MODEL_DIMENSIONS)),
    ).fetchall()
    return budget_dto(budget, usage_rows)
