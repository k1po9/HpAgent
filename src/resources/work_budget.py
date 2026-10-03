"""Cumulative Work limits; retries and requirement revisions share one ledger."""

from __future__ import annotations

import json

from resources.run_budget import RunBudgetConflict, RunBudgetExhausted, _amounts

# Finite defaults. Deployments may change defaults, existing budgets require an explicit adjustment.
DEFAULT_LIMITS = {
    "model_input_tokens": 800000,
    "model_output_tokens": 200000,
    "model_total_tokens": 1000000,
    "model_calls": 400,
    "tool_calls": 400,
    "sources_discovered": 300,
    "source_fetches": 200,
    "research_iterations": 30,
    "bytes_scanned": 1000000000,
    "bytes_returned_to_model": 10000000,
    # Four bounded 128 MiB file-write reservations, settled to actual bytes.
    "bytes_written": 512 * 1024 * 1024,
    "output_file_bytes": 512 * 1024 * 1024,
    "wall_time_ms": 18000000,
}


class WorkBudgetExhausted(RunBudgetExhausted):
    code = "work_budget_exhausted"


class WorkBudgetService:
    @staticmethod
    def create(uow, account_id, work_id):
        uow.execute(
            "INSERT INTO work_budgets(account_id,work_id,limits) VALUES (%s,%s,%s::jsonb)",
            (account_id, work_id, json.dumps(DEFAULT_LIMITS)),
        )

    @staticmethod
    def mutate(uow, run_id, operation_id, amounts=None, *, action="reserve", source=None):
        run = uow.execute(
            "SELECT account_id,work_id,status,requirement_revision,work_control_epoch "
            "FROM runs WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        if run is None or run["work_id"] is None:
            return
        account, work = run["account_id"], run["work_id"]
        budget = uow.execute(
            "SELECT * FROM work_budgets WHERE account_id=%s AND work_id=%s FOR UPDATE",
            (account, work),
        ).fetchone()
        if budget is None:
            raise RuntimeError("Work budget missing")
        rows = uow.execute(
            "SELECT * FROM work_usage_ledger WHERE account_id=%s AND work_id=%s "
            "AND run_id=%s AND operation_id=%s",
            (account, work, run_id, operation_id),
        ).fetchall()
        values = (
            _amounts(amounts)
            if amounts is not None
            else {r["dimension"]: r["reserved_amount"] for r in rows}
        )
        if rows:
            recorded = {
                r["dimension"]: r["reserved_amount"] if action == "reserve" else r["actual_amount"]
                for r in rows
            }
            expected_state = {"reserve": "reserved", "settle": "settled", "release": "released"}[
                action
            ]
            if action == "reserve" or all(r["state"] == expected_state for r in rows):
                if action != "release" and (
                    recorded != values
                    or action == "settle"
                    and any(r["usage_source"] != source for r in rows)
                ):
                    raise RunBudgetConflict("Work operation changed")
                return
            if any(r["state"] != "reserved" for r in rows) or set(recorded) != set(values):
                raise RunBudgetConflict("Work settlement does not match reservation")
        elif action != "reserve":
            raise RunBudgetConflict("Work operation is not reserved")
        used, reserved, limits = dict(budget["used"]), dict(budget["reserved"]), budget["limits"]
        if action == "reserve":
            valid = uow.execute(
                "SELECT 1 FROM works w JOIN accounts a USING(account_id) WHERE w.account_id=%s "
                "AND w.work_id=%s AND a.status='active' AND w.status='active' "
                "AND w.active_coordinator_run_id=%s AND w.current_requirement_revision=%s "
                "AND w.control_epoch=%s",
                (account, work, run_id, run["requirement_revision"], run["work_control_epoch"]),
            ).fetchone()
            if not valid or run["status"] not in {"queued", "running"}:
                raise RunBudgetConflict("Work no longer permits new reservations")
            if any(
                k not in limits or used.get(k, 0) + reserved.get(k, 0) + n > limits[k]
                for k, n in values.items()
            ):
                raise WorkBudgetExhausted("Work cumulative budget exhausted")
            for key, value in values.items():
                reserved[key] = reserved.get(key, 0) + value
                uow.execute(
                    "INSERT INTO work_usage_ledger(account_id,work_id,run_id,operation_id,dimension,state,reserved_amount) "
                    "VALUES (%s,%s,%s,%s,%s,'reserved',%s)",
                    (account, work, run_id, operation_id, key, value),
                )
        else:
            for row in rows:
                key = row["dimension"]
                reserved[key] = reserved.get(key, 0) - row["reserved_amount"]
                if action == "settle":
                    used[key] = used.get(key, 0) + values[key]
                uow.execute(
                    "UPDATE work_usage_ledger SET state=%s,actual_amount=%s,usage_source=%s,settled_at=now() "
                    "WHERE account_id=%s AND work_id=%s AND run_id=%s AND operation_id=%s AND dimension=%s",
                    (
                        "settled" if action == "settle" else "released",
                        values[key] if action == "settle" else None,
                        source,
                        account,
                        work,
                        run_id,
                        operation_id,
                        key,
                    ),
                )
        uow.execute(
            "UPDATE work_budgets SET used=%s::jsonb,reserved=%s::jsonb,version=version+1,updated_at=now() "
            "WHERE account_id=%s AND work_id=%s",
            (json.dumps(used), json.dumps(reserved), account, work),
        )
