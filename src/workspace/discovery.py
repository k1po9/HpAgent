"""Bounded Workspace discovery and file retention diagnostics."""
from __future__ import annotations

from datetime import date
from uuid import UUID

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from workspace.resources import ResourcePolicy


def _item(row):
    return {"node_id": str(row["node_id"]), "name": row["name"],
            "file_id": str(row["file_id"]), "destination_id": str(row["destination_id"])
            if row["destination_id"] else None, "revision": row["revision"],
            "content_type": row["content_type"], "size_bytes": row["size_bytes"],
            "purpose": row["purpose"], "source_run_id": str(row["source_run_id"])
            if row["source_run_id"] else None, "summary": row["summary"], "summary_sha256": row["summary_sha256"],
            "source_task_id": str(row["task_id"])
            if row["task_id"] else None, "created_at": row["created_at"].isoformat()}


class WorkspaceDiscovery:
    def __init__(self, database: object):
        self.database = database
        self.policy = ResourcePolicy(database)

    def search(self, account_id: UUID, *, run_id: UUID | None = None,
               name: str | None = None, summary: str | None = None,
               content_type: str | None = None,
               purpose: str | None = None, task_id: UUID | None = None,
               source_run_id: UUID | None = None, from_date: date | None = None,
               to_date: date | None = None, after: UUID | None = None,
               limit: int = 50) -> dict:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be 1..100")
        with UnitOfWork(self.database) as uow:
            if run_id is not None:
                snapshot = uow.execute(
                    "SELECT subject_kind,subject_id FROM run_resource_snapshots "
                    "WHERE account_id=%s AND run_id=%s AND status='ready'",
                    (account_id, run_id)).fetchone()
                if snapshot is None:
                    raise ResourceNotFound()
            clauses = ["n.account_id=%s", "n.kind='file'", "n.deleted_at IS NULL",
                       "sf.status='ready'", "n.node_id>%s"]
            params: list = [account_id, after or UUID(int=0)]
            for value, sql in ((name, "n.name ILIKE %s ESCAPE '\\'"),
                               (content_type, "sf.content_type=%s"),
                               (purpose, "sf.purpose=%s"),
                               (task_id, "r.task_id=%s"),
                               (source_run_id, "sf.source_run_id=%s"),
                               (from_date, "n.created_at >= %s"),
                               (to_date, "n.created_at < (%s::date + interval '1 day')")):
                if value is not None:
                    clauses.append(sql)
                    params.append("%" + value.replace("\\", "\\\\").replace("%", "\\%")
                                  .replace("_", "\\_") + "%" if sql.startswith("n.name") else value)
            if summary:
                clauses.append("to_tsvector('simple',ws.summary) @@ plainto_tsquery('simple',%s)")
                params.append(summary)
            if run_id is not None:
                clauses.append("EXISTS (SELECT 1 FROM run_resource_candidates c "
                               "WHERE c.run_id=%s AND c.account_id=n.account_id "
                               "AND c.node_id=n.node_id)")
                params.append(run_id)
            query = ("SELECT n.node_id,n.name,n.destination_id,d.current_revision AS revision,"
                     "sf.file_id,sf.content_type,sf.size_bytes,sf.purpose,sf.source_run_id,"
                     "r.task_id,n.created_at,ws.summary,ws.sha256 AS summary_sha256 FROM workspace_nodes n "
                     "LEFT JOIN persistent_file_destinations d ON d.account_id=n.account_id "
                     "AND d.destination_id=n.destination_id "
                     "JOIN stored_files sf ON sf.account_id=n.account_id "
                     "AND sf.file_id=COALESCE(n.file_id,d.current_file_id) "
                     "LEFT JOIN workspace_file_summaries ws ON ws.account_id=sf.account_id "
                     "AND ws.file_id=sf.file_id AND ws.sha256=sf.sha256 "
                     "LEFT JOIN runs r ON r.account_id=sf.account_id "
                     "AND r.run_id=sf.source_run_id WHERE " + " AND ".join(clauses) +
                     " ORDER BY n.node_id LIMIT %s")
            # A Run has at most 500 frozen candidates. Filter current grants before paging.
            if run_id is not None:
                rows = uow.execute(query, tuple(params + [501])).fetchall()
                permitted = []
                for row in rows:
                    if not self.policy._grants(uow, account_id, snapshot["subject_kind"],
                                               snapshot["subject_id"], row["node_id"],
                                               "list_metadata"):
                        continue
                    can_read = bool(self.policy._grants(
                        uow, account_id, snapshot["subject_kind"], snapshot["subject_id"],
                        row["node_id"], "read_content"))
                    if summary and not can_read:
                        continue
                    if not can_read:
                        row = dict(row)
                        row["summary"] = None
                        row["summary_sha256"] = None
                    permitted.append(row)
                rows = permitted
                rows = rows[:limit + 1]
            else:
                rows = uow.execute(query, tuple(params + [limit + 1])).fetchall()
            page = rows[:limit]
            return {"items": [_item(row) for row in page],
                    "next_after": str(page[-1]["node_id"])
                    if len(rows) > limit else None}

    def space(self, account_id: UUID) -> dict:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT count(*) AS physical_files,COALESCE(sum(size_bytes),0) "
                "AS physical_bytes FROM stored_files WHERE account_id=%s "
                "AND status='ready'", (account_id,)).fetchone()
            active = uow.execute(
                "SELECT count(*) AS entries,COALESCE(sum(sf.size_bytes),0) AS bytes "
                "FROM stored_files sf WHERE sf.account_id=%s AND sf.status='ready' "
                "AND EXISTS (SELECT 1 FROM workspace_nodes n "
                "LEFT JOIN persistent_file_destinations d ON d.account_id=n.account_id "
                "AND d.destination_id=n.destination_id WHERE n.account_id=sf.account_id "
                "AND n.deleted_at IS NULL AND n.kind='file' "
                "AND COALESCE(n.file_id,d.current_file_id)=sf.file_id)",
                (account_id,)).fetchone()
            return {"physical_files": row["physical_files"],
                    "physical_bytes": row["physical_bytes"],
                    "files_with_active_entry": active["entries"],
                    "bytes_with_active_entry": active["bytes"]}

    def trace(self, account_id: UUID, node_id: UUID) -> dict:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT n.node_id,n.destination_id,COALESCE(n.file_id,d.current_file_id) "
                "AS file_id,d.current_revision,sf.source_run_id,r.task_id "
                "FROM workspace_nodes n LEFT JOIN persistent_file_destinations d "
                "ON d.account_id=n.account_id AND d.destination_id=n.destination_id "
                "JOIN stored_files sf ON sf.account_id=n.account_id "
                "AND sf.file_id=COALESCE(n.file_id,d.current_file_id) "
                "LEFT JOIN runs r ON r.account_id=sf.account_id "
                "AND r.run_id=sf.source_run_id WHERE n.account_id=%s AND n.node_id=%s "
                "AND n.kind='file'", (account_id, node_id)).fetchone()
            if row is None:
                raise ResourceNotFound()
            versions = uow.execute(
                "SELECT revision,file_id,operation_id FROM persistent_file_revisions "
                "WHERE account_id=%s AND destination_id=%s ORDER BY revision",
                (account_id, row["destination_id"])).fetchall() if row["destination_id"] else []
            saves = uow.execute(
                "SELECT operation_id,source_kind,source_run_id FROM workspace_save_operations "
                "WHERE account_id=%s AND node_id=%s ORDER BY created_at",
                (account_id, node_id)).fetchall()
            usage = uow.execute(
                "SELECT c.run_id,c.fixed_file_id,c.fixed_revision,c.fixed_at,"
                "c.materialized_at,c.first_read_at "
                "FROM run_resource_candidates c WHERE c.account_id=%s AND c.node_id=%s "
                "ORDER BY c.run_id LIMIT 101", (account_id, node_id)).fetchall()
            publication = uow.execute(
                "SELECT operation_id,status,run_id FROM output_publish_operations "
                "WHERE account_id=%s AND file_id=%s",
                (account_id, row["file_id"])).fetchone()
            denials = uow.execute(
                "SELECT run_id,operation,reason,created_at FROM run_resource_denials "
                "WHERE account_id=%s AND node_id=%s ORDER BY created_at DESC LIMIT 100",
                (account_id, node_id)).fetchall()
            return {"node_id": str(node_id), "file_id": str(row["file_id"]),
                    "destination_id": str(row["destination_id"])
                    if row["destination_id"] else None,
                    "current_revision": row["current_revision"],
                    "source_run_id": str(row["source_run_id"])
                    if row["source_run_id"] else None,
                    "source_task_id": str(row["task_id"]) if row["task_id"] else None,
                    "publication": {"operation_id": publication["operation_id"],
                                    "status": publication["status"],
                                    "run_id": str(publication["run_id"])}
                    if publication else None,
                    "denials": [{"run_id": str(item["run_id"]),
                                 "operation": item["operation"],
                                 "reason": item["reason"],
                                 "created_at": item["created_at"].isoformat()}
                                for item in denials],
                    "versions": [{"revision": item["revision"],
                                  "file_id": str(item["file_id"]),
                                  "operation_id": item["operation_id"]} for item in versions],
                    "saves": [{"operation_id": item["operation_id"],
                               "source_kind": item["source_kind"],
                               "source_run_id": str(item["source_run_id"])
                               if item["source_run_id"] else None} for item in saves],
                    "run_usage_truncated": len(usage) > 100,
                    "run_usage": [{"run_id": str(item["run_id"]),
                                   "fixed_file_id": str(item["fixed_file_id"])
                                   if item["fixed_file_id"] else None,
                                   "fixed_revision": item["fixed_revision"],
                                   "fixed_at": item["fixed_at"].isoformat()
                                   if item["fixed_at"] else None,
                                   "materialized_at": item["materialized_at"].isoformat()
                                   if item["materialized_at"] else None,
                                   "first_read_at": item["first_read_at"].isoformat()
                                   if item["first_read_at"] else None}
                                  for item in usage[:100]]}

    def retention(self, account_id: UUID, file_id: UUID) -> dict:
        with UnitOfWork(self.database) as uow:
            file = uow.execute("SELECT file_id,size_bytes,status,source_run_id FROM stored_files "
                               "WHERE account_id=%s AND file_id=%s", (account_id, file_id)).fetchone()
            if file is None:
                raise ResourceNotFound()
            counts = {}
            for label, table, column, extra in (
                ("active_entries", "workspace_nodes", "file_id", " AND deleted_at IS NULL"),
                ("revisions", "persistent_file_revisions", "file_id", ""),
                ("message_references", "message_files", "file_id", ""),
                ("run_bindings", "run_files", "file_id", ""),
                ("fixed_candidates", "run_resource_candidates", "fixed_file_id", ""),
                ("run_access", "run_resource_access", "file_id", ""),
                ("pending_publish", "output_publish_operations", "file_id", " AND status='pending'"),
                ("current_destinations", "persistent_file_destinations", "current_file_id", ""),
            ):
                counts[label] = uow.execute(
                    f"SELECT count(*) AS n FROM {table} WHERE account_id=%s AND {column}=%s{extra}",
                    (account_id, file_id)).fetchone()["n"]
            operations = uow.execute(
                "SELECT operation_id,source_kind,source_run_id FROM workspace_save_operations "
                "WHERE account_id=%s AND node_id IN (SELECT node_id FROM workspace_nodes "
                "WHERE account_id=%s AND file_id=%s) ORDER BY created_at",
                (account_id, account_id, file_id)).fetchall()
            return {"file_id": str(file_id), "status": file["status"],
                    "physical_bytes": file["size_bytes"] or 0, "references": counts,
                    "retained": any(counts.values()), "source_run_id": str(file["source_run_id"])
                    if file["source_run_id"] else None,
                    "save_operations": [{"operation_id": row["operation_id"],
                                         "source_kind": row["source_kind"],
                                         "source_run_id": str(row["source_run_id"])
                                         if row["source_run_id"] else None} for row in operations],
                    "explanation": "Removing a Workspace entry does not release physical bytes; GC reclaims only unreferenced files."}
