"""Conversation/Task resource policy and Run candidate authority."""
from __future__ import annotations

from typing import Any
from uuid import UUID

from uuid6 import uuid7

from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import DomainError, ResourceNotFound

OPERATIONS = frozenset({
    "list_metadata", "read_content", "create_child", "update_content", "delete_entry",
})
CANDIDATE_LIMIT = 500
METADATA_LIMIT = 512_000


class ResourceDenied(DomainError):
    pass


class SnapshotLimitExceeded(DomainError):
    pass


class ResourcePolicy:
    def __init__(self, database: object) -> None:
        self.database = database

    @staticmethod
    def _subject(uow: UnitOfWork, account_id: UUID, kind: str, subject_id: UUID) -> None:
        if kind not in {"conversation", "task"}:
            raise ValueError("unknown policy subject")
        table, column = (("conversations", "conversation_id") if kind == "conversation"
                         else ("tasks", "task_id"))
        if uow.execute(
            f"SELECT 1 FROM {table} WHERE account_id=%s AND {column}=%s",
            (account_id, subject_id),
        ).fetchone() is None:
            raise ResourceNotFound()

    @staticmethod
    def _version(uow: UnitOfWork, account_id: UUID, kind: str, subject_id: UUID) -> int:
        uow.execute(
            "INSERT INTO resource_policy_versions(account_id,subject_kind,subject_id) "
            "VALUES (%s,%s,%s) ON CONFLICT DO NOTHING",
            (account_id, kind, subject_id),
        )
        return int(uow.execute(
            "SELECT version FROM resource_policy_versions WHERE account_id=%s "
            "AND subject_kind=%s AND subject_id=%s FOR UPDATE",
            (account_id, kind, subject_id),
        ).fetchone()["version"])

    @staticmethod
    def _grants(uow: UnitOfWork, account_id: UUID, kind: str, subject_id: UUID,
                node_id: UUID, operation: str) -> list[dict[str, Any]]:
        if operation not in OPERATIONS:
            raise ValueError("unknown resource operation")
        return list(uow.execute(
            "WITH RECURSIVE ancestors AS ("
            " SELECT node_id,parent_id,kind,deleted_at FROM workspace_nodes "
            " WHERE account_id=%s AND node_id=%s"
            " UNION ALL SELECT p.node_id,p.parent_id,p.kind,p.deleted_at "
            " FROM workspace_nodes p JOIN ancestors a ON a.parent_id=p.node_id "
            " WHERE p.account_id=%s) "
            "SELECT g.grant_id,g.node_id FROM resource_grants g "
            "JOIN ancestors a ON a.node_id=g.node_id "
            "WHERE g.account_id=%s AND g.subject_kind=%s AND g.subject_id=%s "
            "AND g.operation=%s AND g.revoked_at IS NULL "
            "AND a.deleted_at IS NULL AND (g.node_id=%s OR (g.recursive AND a.kind='directory')) "
            "AND NOT EXISTS (SELECT 1 FROM ancestors dead WHERE dead.deleted_at IS NOT NULL) "
            "ORDER BY g.created_at,g.grant_id",
            (account_id, node_id, account_id, account_id, kind, subject_id,
             operation, node_id),
        ).fetchall())

    @retryable_transaction
    def grant(self, account_id: UUID, kind: str, subject_id: UUID,
              node_id: UUID, operations: list[str], recursive: bool) -> list[str]:
        if not operations or set(operations) - OPERATIONS:
            raise ValueError("invalid resource operations")
        with UnitOfWork(self.database) as uow:
            self._subject(uow, account_id, kind, subject_id)
            node = uow.execute(
                "SELECT kind FROM workspace_nodes WHERE account_id=%s AND node_id=%s "
                "AND deleted_at IS NULL", (account_id, node_id),
            ).fetchone()
            if node is None or (recursive and node["kind"] != "directory"):
                raise ResourceNotFound()
            self._version(uow, account_id, kind, subject_id)
            ids = []
            for operation in dict.fromkeys(operations):
                grant_id = uuid7()
                uow.execute(
                    "INSERT INTO resource_grants(grant_id,account_id,subject_kind,subject_id,"
                    "node_id,operation,recursive) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (grant_id, account_id, kind, subject_id, node_id, operation, recursive),
                )
                ids.append(str(grant_id))
            uow.execute(
                "UPDATE resource_policy_versions SET version=version+1 WHERE account_id=%s "
                "AND subject_kind=%s AND subject_id=%s",
                (account_id, kind, subject_id),
            )
            return ids

    @retryable_transaction
    def revoke(self, account_id: UUID, grant_id: UUID, commands: Any | None = None) -> list[UUID]:
        """Commit denial first. Returned Runs require control-plane cancellation."""
        with UnitOfWork(self.database) as uow:
            grant = uow.execute(
                "SELECT * FROM resource_grants WHERE account_id=%s AND grant_id=%s FOR UPDATE",
                (account_id, grant_id),
            ).fetchone()
            if grant is None:
                raise ResourceNotFound()
            if grant["revoked_at"] is not None:
                return []
            self._version(uow, account_id, grant["subject_kind"], grant["subject_id"])
            uow.execute("UPDATE resource_grants SET revoked_at=now() WHERE grant_id=%s", (grant_id,))
            uow.execute(
                "UPDATE resource_policy_versions SET version=version+1 WHERE account_id=%s "
                "AND subject_kind=%s AND subject_id=%s",
                (account_id, grant["subject_kind"], grant["subject_id"]),
            )
            affected: set[UUID] = set()
            rows = uow.execute(
                "SELECT DISTINCT r.run_id,a.file_id FROM run_resource_access a "
                "JOIN runs r ON r.run_id=a.run_id WHERE a.account_id=%s "
                "AND a.grant_id=%s AND r.status IN ('queued','running','cancelling')",
                (account_id, grant_id),
            ).fetchall()
            for row in rows:
                if not self._file_authorized_in_uow(uow, account_id,
                                                     row["run_id"], row["file_id"]):
                    affected.add(row["run_id"])
            if commands is not None:
                for run_id in sorted(affected):
                    commands._cancel_run_in_uow(
                        uow, account_id, run_id, f"resource-revoke:{grant_id}:{run_id}"
                    )
            return sorted(affected)

    @retryable_transaction
    def snapshot(self, account_id: UUID, run_id: UUID, conversation_id: UUID) -> None:
        with UnitOfWork(self.database) as uow:
            self.snapshot_in_uow(uow, account_id, run_id, conversation_id)

    def snapshot_in_uow(self, uow: UnitOfWork, account_id: UUID, run_id: UUID,
                        subject_id: UUID, kind: str = "conversation") -> None:
        self._subject(uow, account_id, kind, subject_id)
        version = self._version(uow, account_id, kind, subject_id)
        workspace = uow.execute(
            "SELECT workspace_id,topology_version FROM account_workspaces "
            "WHERE account_id=%s FOR SHARE", (account_id,),
        ).fetchone()
        if workspace is None:
            workspace = {"workspace_id": None, "topology_version": 0}
        # The workspace row is locked while the live tree and policy are read.
        rows = list(uow.execute(
            "WITH RECURSIVE allowed AS ("
            " SELECT n.node_id,n.kind,n.name,n.file_id,n.destination_id "
            " FROM resource_grants g JOIN workspace_nodes n ON n.node_id=g.node_id "
            " WHERE g.account_id=%s AND g.subject_kind=%s AND g.subject_id=%s "
            " AND g.operation='list_metadata' AND g.revoked_at IS NULL "
            " AND n.account_id=%s AND n.workspace_id=%s AND n.deleted_at IS NULL "
            " AND (n.kind='file' OR g.recursive)"
            " UNION ALL SELECT child.node_id,child.kind,child.name,child.file_id,"
            " child.destination_id FROM allowed a JOIN workspace_nodes child "
            " ON child.parent_id=a.node_id WHERE a.kind='directory' "
            " AND child.account_id=%s AND child.deleted_at IS NULL) "
            "SELECT DISTINCT a.node_id,a.name,COALESCE(a.file_id,d.current_file_id) AS file_id,"
            "sf.content_type,sf.size_bytes "
            "FROM allowed a LEFT JOIN persistent_file_destinations d "
            "ON d.account_id=%s AND d.destination_id=a.destination_id "
            "LEFT JOIN stored_files sf ON sf.account_id=%s "
            "AND sf.file_id=COALESCE(a.file_id,d.current_file_id) "
            "WHERE a.kind='file' ORDER BY a.name,a.node_id LIMIT %s",
            (account_id, kind, subject_id, account_id, workspace["workspace_id"],
             account_id, account_id, account_id, CANDIDATE_LIMIT + 1),
        ).fetchall())
        if len(rows) > CANDIDATE_LIMIT:
            raise SnapshotLimitExceeded("candidate count exceeds 500; narrow the grant")
        if sum(len(str(r["name"]).encode()) + 128 for r in rows) > METADATA_LIMIT:
            raise SnapshotLimitExceeded("candidate metadata exceeds 512 KiB")
        uow.execute(
            "INSERT INTO run_resource_snapshots(run_id,account_id,subject_kind,subject_id,"
            "policy_version,topology_version,status,candidate_count,candidate_limit,completed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'ready',%s,%s,now())",
            (run_id, account_id, kind, subject_id, version,
             workspace["topology_version"], len(rows), CANDIDATE_LIMIT),
        )
        names = {str(row["logical_name"]).casefold() for row in uow.execute(
            "SELECT logical_name FROM run_files WHERE run_id=%s", (run_id,)
        ).fetchall()}
        for row in rows:
            base = str(row["name"])
            name = base
            if name.casefold() in names:
                name = f"{row['node_id'].hex}_{base}"[:255]
            names.add(name.casefold())
            uow.execute(
                "INSERT INTO run_resource_candidates(run_id,account_id,node_id,logical_name,"
                "display_name,content_type,size_bytes) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (run_id, account_id, row["node_id"], name, base,
                 row["content_type"], row["size_bytes"]),
            )

    def candidates(self, account_id: UUID, run_id: UUID, after: str | None = None,
                   limit: int = 50) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            snapshot = uow.execute(
                "SELECT s.* FROM run_resource_snapshots s JOIN runs r ON r.run_id=s.run_id "
                "WHERE s.account_id=%s AND s.run_id=%s AND r.account_id=%s "
                "AND s.status='ready'", (account_id, run_id, account_id),
            ).fetchone()
            if snapshot is None:
                raise ResourceNotFound()
            rows = uow.execute(
                "SELECT node_id,logical_name,display_name,content_type,size_bytes,"
                "fixed_file_id,first_read_at FROM run_resource_candidates "
                "WHERE run_id=%s AND logical_name>%s ORDER BY logical_name LIMIT %s",
                (run_id, after or "", min(max(limit, 1), 100) + 1),
            ).fetchall()
            page = list(rows[:limit])
            return {"count": snapshot["candidate_count"],
                    "next": page[-1]["logical_name"] if len(rows) > limit else None,
                    "candidates": [{"node_id": str(r["node_id"]),
                     "logical_name": r["logical_name"], "name": r["display_name"],
                     "content_type": r["content_type"], "size_bytes": r["size_bytes"],
                     "fixed": r["fixed_file_id"] is not None,
                     "read": r["first_read_at"] is not None} for r in page]}

    def select(self, account_id: UUID, run_id: UUID, node_id: UUID) -> dict[str, Any]:
        try:
            return self._select(account_id, run_id, node_id)
        except ResourceDenied as exc:
            self._record_denial(account_id, run_id, "select", str(exc), node_id=node_id)
            raise

    @retryable_transaction
    def _select(self, account_id: UUID, run_id: UUID, node_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            run = uow.execute(
                "SELECT status,conversation_id FROM runs WHERE account_id=%s AND run_id=%s FOR UPDATE",
                (account_id, run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running"}:
                raise ResourceDenied("Run is not active")
            candidate = uow.execute(
                "SELECT c.* FROM run_resource_candidates c JOIN run_resource_snapshots s "
                "ON s.run_id=c.run_id WHERE c.account_id=%s AND c.run_id=%s "
                "AND c.node_id=%s AND s.status='ready' FOR UPDATE OF c",
                (account_id, run_id, node_id),
            ).fetchone()
            if candidate is None:
                raise ResourceDenied("resource is not a frozen Run candidate")
            snapshot = uow.execute(
                "SELECT subject_kind,subject_id FROM run_resource_snapshots WHERE run_id=%s",
                (run_id,),
            ).fetchone()
            grants = self._grants(uow, account_id, snapshot["subject_kind"],
                                  snapshot["subject_id"], node_id, "read_content")
            if not grants:
                raise ResourceDenied("current read_content permission is absent")
            if candidate["fixed_file_id"] is None:
                node = uow.execute(
                    "SELECT COALESCE(n.file_id,d.current_file_id) AS file_id,"
                    "d.current_revision AS revision FROM workspace_nodes n "
                    "LEFT JOIN persistent_file_destinations d ON d.account_id=n.account_id "
                    "AND d.destination_id=n.destination_id "
                    "WHERE n.account_id=%s AND n.node_id=%s "
                    "AND n.kind='file' AND n.deleted_at IS NULL",
                    (account_id, node_id),
                ).fetchone()
                if node is None or node["file_id"] is None:
                    raise ResourceDenied("resource has no ready file version")
                file_id = node["file_id"]
                file = uow.execute(
                    "SELECT status FROM stored_files WHERE account_id=%s AND file_id=%s",
                    (account_id, file_id),
                ).fetchone()
                if file is None or file["status"] != "ready":
                    raise ResourceDenied("resource has no ready file version")
                uow.execute(
                    "UPDATE run_resource_candidates SET fixed_file_id=%s,fixed_revision=%s,"
                    "fixed_at=now() WHERE run_id=%s AND node_id=%s",
                    (file_id, node["revision"], run_id, node_id),
                )
            else:
                file_id = candidate["fixed_file_id"]
            grant_id = grants[0]["grant_id"]
            uow.execute(
                "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,node_id,basis,grant_id) "
                "VALUES (%s,%s,%s,%s,%s,'workspace_grant',%s) ON CONFLICT DO NOTHING",
                (uuid7(), account_id, run_id, file_id, node_id, grant_id),
            )
            # One immutable object has one Run input binding. An entry keeps its own access fact.
            existing = uow.execute(
                "SELECT logical_name FROM run_files WHERE account_id=%s AND run_id=%s "
                "AND file_id=%s AND direction='input'", (account_id, run_id, file_id),
            ).fetchone()
            if existing is None:
                uow.execute(
                    "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,logical_name) "
                    "VALUES (%s,%s,%s,%s,'input',%s)",
                    (account_id, run["conversation_id"], run_id, file_id,
                     candidate["logical_name"]),
                )
            return {"file_id": str(file_id),
                    "logical_name": existing["logical_name"] if existing else candidate["logical_name"],
                    "grant_id": str(grant_id)}

    def _file_authorized_in_uow(self, uow: UnitOfWork, account_id: UUID,
                                run_id: UUID, file_id: UUID) -> bool:
        if uow.execute(
            "SELECT 1 FROM run_files rf JOIN stored_files sf "
            "ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
            "WHERE rf.account_id=%s AND rf.run_id=%s AND rf.file_id=%s "
            "AND rf.direction='output' AND sf.source_run_id=%s",
            (account_id, run_id, file_id, run_id),
        ).fetchone():
            return True
        accesses = uow.execute(
            "SELECT a.basis,a.node_id,s.subject_kind,s.subject_id FROM run_resource_access a "
            "LEFT JOIN run_resource_snapshots s ON s.run_id=a.run_id "
            "WHERE a.account_id=%s AND a.run_id=%s AND a.file_id=%s",
            (account_id, run_id, file_id),
        ).fetchall()
        for access in accesses:
            if access["basis"] == "run_output":
                return True
            if access["basis"] == "explicit_attachment" and uow.execute(
                "SELECT 1 FROM conversation_resource_files crf JOIN runs r "
                "ON r.account_id=crf.account_id AND r.conversation_id=crf.conversation_id "
                "WHERE r.account_id=%s AND r.run_id=%s AND crf.file_id=%s "
                "AND crf.available=true",
                (account_id, run_id, file_id),
            ).fetchone():
                return True
            if access["node_id"] and self._grants(
                uow, account_id, access["subject_kind"], access["subject_id"],
                access["node_id"], "read_content"
            ):
                return True
        return False

    def check_file(self, account_id: UUID, run_id: UUID, file_id: UUID,
                   *, mark_read: bool = True) -> None:
        try:
            self._check_file(account_id, run_id, file_id, mark_read=mark_read)
        except ResourceDenied as exc:
            self._record_denial(account_id, run_id, "read_content", str(exc), file_id=file_id)
            raise

    def _record_denial(self, account_id: UUID, run_id: UUID, operation: str,
                       reason: str, *, node_id: UUID | None = None,
                       file_id: UUID | None = None) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "INSERT INTO run_resource_denials(denial_id,account_id,run_id,operation,"
                "node_id,file_id,reason) SELECT %s,%s,%s,%s,%s,%s,%s "
                "WHERE EXISTS (SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s)",
                (uuid7(), account_id, run_id, operation, node_id, file_id, reason,
                 account_id, run_id),
            )

    def _check_file(self, account_id: UUID, run_id: UUID, file_id: UUID,
                    *, mark_read: bool) -> None:
        with UnitOfWork(self.database) as uow:
            run = uow.execute(
                "SELECT status FROM runs WHERE account_id=%s AND run_id=%s",
                (account_id, run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running"}:
                raise ResourceDenied("Run is not active")
            if not self._file_authorized_in_uow(uow, account_id, run_id, file_id):
                raise ResourceDenied("Run has no current read authority for file")
            if not mark_read:
                return
            uow.execute(
                "UPDATE run_resource_access SET first_read_at=COALESCE(first_read_at,now()) "
                "WHERE account_id=%s AND run_id=%s AND file_id=%s",
                (account_id, run_id, file_id),
            )
            uow.execute(
                "UPDATE run_resource_candidates SET first_read_at=COALESCE(first_read_at,now()) "
                "WHERE account_id=%s AND run_id=%s AND fixed_file_id=%s",
                (account_id, run_id, file_id),
            )

    def grants(self, account_id: UUID, kind: str, subject_id: UUID) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            self._subject(uow, account_id, kind, subject_id)
            rows = uow.execute(
                "SELECT g.grant_id,g.node_id,n.name,n.kind,g.operation,g.recursive,g.created_at "
                "FROM resource_grants g JOIN workspace_nodes n ON n.node_id=g.node_id "
                "WHERE g.account_id=%s AND g.subject_kind=%s AND g.subject_id=%s "
                "AND g.revoked_at IS NULL ORDER BY g.created_at,g.grant_id",
                (account_id, kind, subject_id),
            ).fetchall()
            return [{"grant_id": str(row["grant_id"]), "node_id": str(row["node_id"]),
                     "name": row["name"], "kind": row["kind"],
                     "operation": row["operation"], "recursive": row["recursive"]}
                    for row in rows]

    def conversation_files(self, account_id: UUID,
                           conversation_id: UUID) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            self._subject(uow, account_id, "conversation", conversation_id)
            rows = uow.execute(
                "SELECT crf.file_id,sf.display_name,crf.available FROM conversation_resource_files crf "
                "JOIN stored_files sf ON sf.account_id=crf.account_id AND sf.file_id=crf.file_id "
                "WHERE crf.account_id=%s AND crf.conversation_id=%s "
                "ORDER BY crf.created_at,crf.file_id",
                (account_id, conversation_id),
            ).fetchall()
            return [{"file_id": str(row["file_id"]), "name": row["display_name"],
                     "available": row["available"]} for row in rows]

    @retryable_transaction
    def revoke_conversation_file(self, account_id: UUID, conversation_id: UUID,
                                 file_id: UUID, commands: Any) -> list[UUID]:
        with UnitOfWork(self.database) as uow:
            self._subject(uow, account_id, "conversation", conversation_id)
            changed = uow.execute(
                "UPDATE conversation_resource_files SET available=false,revoked_at=now() "
                "WHERE account_id=%s AND conversation_id=%s AND file_id=%s "
                "AND available=true RETURNING file_id",
                (account_id, conversation_id, file_id),
            ).fetchone()
            if changed is None:
                raise ResourceNotFound()
            rows = uow.execute(
                "SELECT DISTINCT a.run_id FROM run_resource_access a JOIN runs r "
                "ON r.run_id=a.run_id WHERE a.account_id=%s AND r.conversation_id=%s "
                "AND a.file_id=%s AND a.basis='explicit_attachment' "
                "AND r.status IN ('queued','running','cancelling')",
                (account_id, conversation_id, file_id),
            ).fetchall()
            affected = [row["run_id"] for row in rows if not self._file_authorized_in_uow(
                uow, account_id, row["run_id"], file_id
            )]
            for run_id in affected:
                commands._cancel_run_in_uow(
                    uow, account_id, run_id,
                    f"attachment-revoke:{conversation_id}:{file_id}:{run_id}"[:128],
                )
            return affected

    def copy_retry_in_uow(self, uow: UnitOfWork, source_run_id: UUID,
                          target_run_id: UUID) -> None:
        source = uow.execute(
            "SELECT * FROM run_resource_snapshots WHERE run_id=%s AND status='ready'",
            (source_run_id,),
        ).fetchone()
        if source is None:
            raise ResourceDenied("source Run has no complete candidate snapshot")
        uow.execute(
            "INSERT INTO run_resource_snapshots(run_id,account_id,subject_kind,subject_id,"
            "policy_version,topology_version,status,candidate_count,candidate_limit,completed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'ready',%s,%s,now())",
            (target_run_id, source["account_id"], source["subject_kind"],
             source["subject_id"], source["policy_version"], source["topology_version"],
             source["candidate_count"], source["candidate_limit"]),
        )
        uow.execute(
            "INSERT INTO run_resource_candidates(run_id,account_id,node_id,logical_name,"
            "display_name,content_type,size_bytes,fixed_file_id,fixed_revision,fixed_at) "
            "SELECT %s,account_id,node_id,logical_name,display_name,content_type,size_bytes,"
            "fixed_file_id,fixed_revision,fixed_at FROM run_resource_candidates WHERE run_id=%s",
            (target_run_id, source_run_id),
        )
        rows = uow.execute(
            "SELECT account_id,file_id,node_id,basis,grant_id FROM run_resource_access "
            "WHERE run_id=%s AND basis<>'run_output'", (source_run_id,),
        ).fetchall()
        for row in rows:
            if row["basis"] == "explicit_attachment" and not uow.execute(
                "SELECT 1 FROM conversation_resource_files crf JOIN runs r "
                "ON r.account_id=crf.account_id AND r.conversation_id=crf.conversation_id "
                "WHERE r.run_id=%s AND crf.file_id=%s AND crf.available=true",
                (source_run_id, row["file_id"]),
            ).fetchone():
                raise ResourceDenied("retry attachment was revoked")
            if row["basis"] == "workspace_grant" and not self._grants(
                uow, row["account_id"], source["subject_kind"], source["subject_id"],
                row["node_id"], "read_content"
            ):
                raise ResourceDenied("retry input permission was revoked")
            uow.execute(
                "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,node_id,"
                "basis,grant_id) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (uuid7(), row["account_id"], target_run_id, row["file_id"],
                 row["node_id"], row["basis"], row["grant_id"]),
            )

    def stop_invalid_in_uow(self, uow: UnitOfWork, account_id: UUID,
                            commands: Any, reason: str) -> list[UUID]:
        """Fence active Runs whose selected Workspace inputs lost every basis."""
        rows = uow.execute(
            "SELECT DISTINCT a.run_id,a.file_id FROM run_resource_access a "
            "JOIN runs r ON r.run_id=a.run_id WHERE a.account_id=%s "
            "AND a.basis='workspace_grant' AND r.status IN ('queued','running','cancelling')",
            (account_id,),
        ).fetchall()
        affected = {row["run_id"] for row in rows if not self._file_authorized_in_uow(
            uow, account_id, row["run_id"], row["file_id"]
        )}
        for run_id in sorted(affected):
            commands._cancel_run_in_uow(
                uow, account_id, run_id, f"resource-topology:{reason}:{run_id}"
            )
        return sorted(affected)
