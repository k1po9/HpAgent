"""Shared reference check and GC claim protocol for tenant file bytes."""
from __future__ import annotations

from uuid import UUID

from persistence.uow import UnitOfWork


def claim_file_deletion(
    uow: UnitOfWork, account_id: UUID, file_id: UUID,
) -> str:
    row = uow.execute(
        "SELECT status FROM stored_files WHERE account_id=%s AND file_id=%s FOR UPDATE",
        (account_id, file_id),
    ).fetchone()
    if row is None:
        return "not_found"
    if row["status"] == "deleted":
        return "deleted"
    retained = uow.execute(
        "SELECT EXISTS(SELECT 1 FROM message_files WHERE account_id=%s AND file_id=%s) "
        "OR EXISTS(SELECT 1 FROM run_files WHERE account_id=%s AND file_id=%s) "
        "OR EXISTS(SELECT 1 FROM workspace_nodes WHERE account_id=%s AND file_id=%s "
        "AND deleted_at IS NULL) "
        "OR EXISTS(SELECT 1 FROM persistent_file_destinations "
        "WHERE account_id=%s AND current_file_id=%s) "
        "OR EXISTS(SELECT 1 FROM persistent_file_revisions "
        "WHERE account_id=%s AND file_id=%s) "
        "OR EXISTS(SELECT 1 FROM run_resource_candidates "
        "WHERE account_id=%s AND fixed_file_id=%s) "
        "OR EXISTS(SELECT 1 FROM run_resource_access "
        "WHERE account_id=%s AND file_id=%s) "
        "OR EXISTS(SELECT 1 FROM output_publish_operations "
        "WHERE account_id=%s AND file_id=%s AND status='pending') AS retained",
        (account_id, file_id) * 8,
    ).fetchone()["retained"]
    if retained:
        return "bound"
    uow.execute(
        "UPDATE stored_files SET status='deleted',deleted_at=now(),expires_at=now() "
        "WHERE account_id=%s AND file_id=%s",
        (account_id, file_id),
    )
    return "deleted"
