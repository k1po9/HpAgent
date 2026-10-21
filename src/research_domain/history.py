"""Fixed, bounded Research history from the Run's authorized Workspace candidates."""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from persistence.uow import UnitOfWork
from research_domain.context import invocation_id, research_key
from workspace.resources import ResourceDenied, ResourcePolicy

HISTORY_BUDGET_BYTES = 8192


def freeze_history(uow: UnitOfWork, account_id: UUID, run_id: UUID) -> None:
    if uow.execute(
        "SELECT 1 FROM research_history_baselines WHERE research_id=%s AND run_id=%s",
        (research_key(run_id), run_id),
    ).fetchone():
        return
    run = uow.execute("SELECT work_id,created_at,input_snapshot FROM runs WHERE run_id=%s", (run_id,)).fetchone()
    policy = ResourcePolicy(None)
    candidates = uow.execute(
        "SELECT c.node_id,sf.file_id,rr.run_id AS source_run_id,rr.invocation_id,rr.snapshot,r.created_at,r.requirement_revision AS source_requirement_revision "
        "FROM run_resource_candidates c JOIN workspace_nodes n ON n.node_id=c.node_id "
        "LEFT JOIN persistent_file_destinations d ON d.account_id=n.account_id "
        "AND d.destination_id=n.destination_id "
        "JOIN stored_files sf ON sf.account_id=c.account_id "
        "AND sf.file_id=COALESCE(c.fixed_file_id,n.file_id,d.current_file_id) "
        "JOIN research_reports rr ON rr.run_id=sf.source_run_id AND (rr.markdown_file_id=sf.file_id OR rr.invocation_id IS NULL) "
        "JOIN runs r ON r.run_id=rr.run_id "
        "WHERE c.run_id=%s AND r.work_id=%s AND r.status='succeeded' "
        "AND r.created_at<%s AND (%s OR r.run_id=%s) "
        "ORDER BY r.created_at DESC,r.run_id DESC LIMIT 20",
        (run_id, run["work_id"], run["created_at"],
         run['input_snapshot'].get('requirement', {}).get('timing', {}).get('kind') != 'daily',
         run['input_snapshot'].get('daily_baseline_run_id')),
    ).fetchall()
    allowed_nodes = None
    if invocation_id():
        descriptor = uow.execute(
            "SELECT descriptor FROM capability_invocations WHERE account_id=%s AND run_id=%s AND invocation_id=%s",
            (account_id, run_id, invocation_id()),
        ).fetchone()["descriptor"]
        allowed_nodes = set(descriptor["resource_scope"]["node_ids"])
    selected = []
    used = 0
    for row in candidates:
        if allowed_nodes is not None and str(row["node_id"]) not in allowed_nodes:
            continue
        if not policy._grants(
            uow, account_id, "work", run["work_id"], row["node_id"], "read_content"
        ):
            continue
        snapshot = (
            row["snapshot"] if isinstance(row["snapshot"], dict) else json.loads(row["snapshot"])
        )
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
        if not snapshot or used + len(encoded) > HISTORY_BUDGET_BYTES:
            continue
        selected.append(
            {
                "run_id": str(row["source_run_id"]),
                "invocation_id": row["invocation_id"],
                "source_requirement_revision": row["source_requirement_revision"],
                "node_id": str(row["node_id"]),
                "file_id": str(row["file_id"]),
                "snapshot": snapshot,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "reason": "same_work_recent_authorized",
                "bytes": len(encoded),
            }
        )
        used += len(encoded)
        if len(selected) == 3:
            break
    uow.execute(
        "INSERT INTO research_history_baselines(run_id,invocation_id,account_id,budget_bytes,selected) "
        "VALUES (%s,%s,%s,%s,%s::jsonb)",
        (
            run_id,
            invocation_id(),
            account_id,
            HISTORY_BUDGET_BYTES,
            json.dumps(selected, ensure_ascii=False),
        ),
    )


def authorized_history(
    uow: UnitOfWork, run_id: UUID, *, instance_id: str | None = None
) -> list[dict]:
    row = uow.execute(
        "SELECT h.account_id,h.selected,r.work_id FROM research_history_baselines h "
        "JOIN runs r ON r.run_id=h.run_id WHERE h.research_id=%s AND h.run_id=%s",
        (instance_id or research_key(run_id), run_id),
    ).fetchone()
    if row is None:
        raise ResourceDenied("Research history baseline is missing")
    selected = row["selected"] if isinstance(row["selected"], list) else json.loads(row["selected"])
    policy = ResourcePolicy(None)
    for item in selected:
        node_id = UUID(item["node_id"])
        if not policy._grants(
            uow, row["account_id"], "work", row["work_id"], node_id, "read_content"
        ):
            raise ResourceDenied("Research history read_content was revoked")
        current = uow.execute(
            "SELECT 1 FROM workspace_nodes n JOIN stored_files sf "
            "ON sf.account_id=n.account_id AND sf.file_id=%s "
            "WHERE n.account_id=%s AND n.node_id=%s "
            "AND n.deleted_at IS NULL AND sf.status='ready'",
            (UUID(item["file_id"]), row["account_id"], node_id),
        ).fetchone()
        if current is None:
            raise ResourceDenied("Research history entry changed")
        encoded = json.dumps(item["snapshot"], sort_keys=True, ensure_ascii=False).encode()
        if hashlib.sha256(encoded).hexdigest() != item["sha256"]:
            raise ResourceDenied("Research history snapshot changed")
    return selected
