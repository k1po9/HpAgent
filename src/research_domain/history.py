"""Fixed, bounded Research history from the Run's authorized Workspace candidates."""
from __future__ import annotations

import hashlib
import json
from uuid import UUID

from persistence.uow import UnitOfWork
from workspace.resources import ResourceDenied, ResourcePolicy

HISTORY_BUDGET_BYTES = 8192


def freeze_history(uow: UnitOfWork, account_id: UUID, run_id: UUID) -> None:
    if uow.execute("SELECT 1 FROM research_history_baselines WHERE run_id=%s", (run_id,)).fetchone():
        return
    run = uow.execute("SELECT task_id,created_at FROM runs WHERE run_id=%s", (run_id,)).fetchone()
    policy = ResourcePolicy(None)
    candidates = uow.execute(
        "SELECT c.node_id,sf.file_id,rr.run_id AS source_run_id,rr.snapshot,r.created_at "
        "FROM run_resource_candidates c JOIN workspace_nodes n ON n.node_id=c.node_id "
        "LEFT JOIN persistent_file_destinations d ON d.account_id=n.account_id "
        "AND d.destination_id=n.destination_id "
        "JOIN stored_files sf ON sf.account_id=c.account_id "
        "AND sf.file_id=COALESCE(n.file_id,d.current_file_id) "
        "JOIN research_reports rr ON rr.run_id=sf.source_run_id "
        "JOIN runs r ON r.run_id=rr.run_id "
        "WHERE c.run_id=%s AND r.task_id=%s AND r.status='completed' "
        "AND r.created_at<%s ORDER BY r.created_at DESC,r.run_id DESC LIMIT 20",
        (run_id, run["task_id"], run["created_at"]),
    ).fetchall()
    selected = []
    used = 0
    for row in candidates:
        if not policy._grants(uow, account_id, "task", run["task_id"],
                              row["node_id"], "read_content"):
            continue
        snapshot = row["snapshot"] if isinstance(row["snapshot"], dict) else json.loads(row["snapshot"])
        encoded = json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()
        if not snapshot or used + len(encoded) > HISTORY_BUDGET_BYTES:
            continue
        selected.append({"run_id": str(row["source_run_id"]),
                         "node_id": str(row["node_id"]), "file_id": str(row["file_id"]),
                         "snapshot": snapshot, "sha256": hashlib.sha256(encoded).hexdigest(),
                         "reason": "same_task_recent_authorized", "bytes": len(encoded)})
        used += len(encoded)
        if len(selected) == 3:
            break
    uow.execute(
        "INSERT INTO research_history_baselines(run_id,account_id,budget_bytes,selected) "
        "VALUES (%s,%s,%s,%s::jsonb)",
        (run_id, account_id, HISTORY_BUDGET_BYTES, json.dumps(selected, ensure_ascii=False)),
    )


def authorized_history(uow: UnitOfWork, run_id: UUID) -> list[dict]:
    row = uow.execute(
        "SELECT h.account_id,h.selected,r.task_id FROM research_history_baselines h "
        "JOIN runs r ON r.run_id=h.run_id WHERE h.run_id=%s", (run_id,),
    ).fetchone()
    if row is None:
        raise ResourceDenied("Research history baseline is missing")
    selected = row["selected"] if isinstance(row["selected"], list) else json.loads(row["selected"])
    policy = ResourcePolicy(None)
    for item in selected:
        node_id = UUID(item["node_id"])
        if not policy._grants(uow, row["account_id"], "task", row["task_id"],
                              node_id, "read_content"):
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
