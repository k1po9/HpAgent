"""Persisted branch facts and restricted resource scope; no Temporal context."""

from run_domain.execution import lock_execution
from run_domain.execution_context import execution_fence


class DelegationDenied(ValueError):
    pass


def current_execution(uow):
    fence = execution_fence.get()
    if fence is None:
        return None
    return lock_execution(uow, fence[0], fence[1], fence[2])


def child_scope(uow):
    execution = current_execution(uow)
    return execution["resource_scope"] if execution and execution["role"] == "subagent" else None


def require_root_write(uow):
    if child_scope(uow) is not None:
        raise DelegationDenied("Subagent cannot change long-term resources or permissions")


def branch_projection(uow, account_id, run_id):
    """Usage is a projection of original reservations, never another charge."""
    from work_domain.persistence import dto

    rows = uow.execute(
        "SELECT execution_id,parent_execution_id,branch_key,branch_attempt,status,result_ref,error_code,"
        "context_manifest->'brief'->'required' AS required FROM run_executions "
        "WHERE account_id=%s AND run_id=%s AND role='subagent' ORDER BY branch_key,branch_attempt",
        (account_id, run_id),
    ).fetchall()
    for row in rows:
        usage = uow.execute(
            "SELECT dimension,sum(CASE WHEN state='settled' THEN actual_amount ELSE 0 END) AS used,"
            "sum(CASE WHEN state='reserved' THEN reserved_amount ELSE 0 END) AS reserved "
            "FROM run_usage_ledger WHERE run_id=%s AND execution_id=%s GROUP BY dimension",
            (run_id, row["execution_id"]),
        ).fetchall()
        row["usage"] = {
            v["dimension"]: {"used": int(v["used"]), "reserved": int(v["reserved"])} for v in usage
        }
    return dto(rows)


def required_branch_gaps(uow, account_id, run_id):
    return [
        row["branch_key"]
        for row in uow.execute(
            "SELECT b->>'branch_key' AS branch_key FROM execution_delegations d, jsonb_array_elements(d.briefs) b "
            "WHERE d.account_id=%s AND d.run_id=%s AND (b->>'required')::boolean AND NOT EXISTS("
            "SELECT 1 FROM run_executions e WHERE e.parent_execution_id=d.execution_id "
            "AND e.branch_key=b->>'branch_key' AND e.status='succeeded')",
            (account_id, run_id),
        ).fetchall()
    ]
