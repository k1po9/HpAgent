"""Account-owned Workspace tree and zero-copy immutable file entries."""
from __future__ import annotations

import hashlib
import json
import unicodedata
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import DomainError, ResourceNotFound


class WorkspaceConflict(DomainError):
    pass


class WorkspaceNotFound(ResourceNotFound):
    pass


class WorkspaceVersionConflict(WorkspaceConflict):
    def __init__(self, current: dict[str, Any]):
        super().__init__("Workspace version changed")
        self.current = current


def normalize_name(value: str) -> tuple[str, str]:
    name = unicodedata.normalize("NFC", value)
    if (not name or name != name.strip() or name in {".", ".."}
            or "/" in name or "\\" in name or any(ord(c) < 32 for c in name)
            or len(name) > 255):
        raise WorkspaceConflict("invalid Workspace name")
    key = name.casefold()
    if len(key) > 255:
        raise WorkspaceConflict("normalized Workspace name is too long")
    return name, key


class WorkspaceCatalog:
    def __init__(self, database: object, commands: object | None = None) -> None:
        self.database = database
        self.commands = commands

    @retryable_transaction
    def initialize(self, account_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s::text,421))",
                (account_id,),
            )
            if uow.execute(
                "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'", (account_id,)
            ).fetchone() is None:
                raise WorkspaceNotFound()
            existing = uow.execute(
                "SELECT workspace_id FROM account_workspaces WHERE account_id=%s",
                (account_id,),
            ).fetchone()
            if existing:
                return self._tree_in_uow(uow, account_id, existing["workspace_id"])
            workspace_id, root_id = uuid7(), uuid7()
            uow.execute(
                "INSERT INTO account_workspaces(workspace_id,account_id) VALUES (%s,%s)",
                (workspace_id, account_id),
            )
            uow.execute(
                "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,"
                "kind,name,name_key) VALUES (%s,%s,%s,NULL,'directory','','')",
                (root_id, account_id, workspace_id),
            )
            for name in ("资料", "任务", "成果"):
                display, key = normalize_name(name)
                uow.execute(
                    "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,"
                    "kind,name,name_key) VALUES (%s,%s,%s,%s,'directory',%s,%s)",
                    (uuid7(), account_id, workspace_id, root_id, display, key),
                )
            return self._tree_in_uow(uow, account_id, workspace_id)

    def tree(self, account_id: UUID) -> dict[str, Any]:
        return self.initialize(account_id)

    @staticmethod
    def _tree_in_uow(uow: UnitOfWork, account_id: UUID, workspace_id: UUID) -> dict[str, Any]:
        rows = uow.execute(
            "SELECT n.node_id,n.parent_id,n.kind,n.name,n.destination_id,"
            "d.current_revision,COALESCE(n.file_id,d.current_file_id) AS file_id,n.created_at,"
            "sf.purpose,sf.conversation_id AS source_conversation_id,"
            "sf.source_run_id,sf.sha256,sf.size_bytes "
            "FROM workspace_nodes n LEFT JOIN persistent_file_destinations d "
            "ON d.account_id=n.account_id AND d.destination_id=n.destination_id "
            "LEFT JOIN stored_files sf ON sf.account_id=n.account_id "
            "AND sf.file_id=COALESCE(n.file_id,d.current_file_id) "
            "WHERE n.account_id=%s AND n.workspace_id=%s "
            "AND n.deleted_at IS NULL ORDER BY n.parent_id NULLS FIRST,n.name_key,n.node_id",
            (account_id, workspace_id),
        ).fetchall()
        return {"workspace_id": str(workspace_id), "root_id": str(rows[0]["node_id"]),
                "nodes": [
                    {"node_id": str(row["node_id"]),
                     "parent_id": str(row["parent_id"]) if row["parent_id"] else None,
                     "kind": row["kind"], "name": row["name"],
                     "file_id": str(row["file_id"]) if row["file_id"] else None,
                     "destination_id": str(row["destination_id"])
                         if row["destination_id"] else None,
                     "revision": row["current_revision"],
                     "source": ({"purpose": row["purpose"],
                       "conversation_id": str(row["source_conversation_id"])
                           if row["source_conversation_id"] else None,
                       "run_id": str(row["source_run_id"]) if row["source_run_id"] else None,
                       "sha256": row["sha256"], "size_bytes": row["size_bytes"]}
                       if row["file_id"] else None)}
                    for row in rows]}

    def _workspace(self, uow: UnitOfWork, account_id: UUID) -> UUID:
        row = uow.execute(
            "SELECT workspace_id FROM account_workspaces WHERE account_id=%s FOR UPDATE",
            (account_id,),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound()
        return row["workspace_id"]

    @retryable_transaction
    def create_directory(self, account_id: UUID, parent_id: UUID, name: str) -> UUID:
        display, key = normalize_name(name)
        with UnitOfWork(self.database) as uow:
            workspace_id = self._workspace(uow, account_id)
            node_id = uuid7()
            uow.execute(
                "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,"
                "kind,name,name_key) VALUES (%s,%s,%s,%s,'directory',%s,%s)",
                (node_id, account_id, workspace_id, parent_id, display, key),
            )
            return node_id

    @retryable_transaction
    def save_file(
        self, account_id: UUID, parent_id: UUID, file_id: UUID,
        name: str, operation_id: str, *, agent_run_id: UUID | None = None,
    ) -> UUID:
        if not operation_id or len(operation_id) > 200:
            raise WorkspaceConflict("invalid operation ID")
        display, key = normalize_name(name)
        intent = hashlib.sha256(json.dumps(
            [str(parent_id), str(file_id), display], separators=(",", ":")
        ).encode()).hexdigest()
        with UnitOfWork(self.database) as uow:
            workspace_id = self._workspace(uow, account_id)
            previous = uow.execute(
                "SELECT node_id,intent_sha256 FROM workspace_save_operations "
                "WHERE account_id=%s AND operation_id=%s", (account_id, operation_id),
            ).fetchone()
            if previous:
                if previous["intent_sha256"] != intent:
                    raise WorkspaceConflict("operation ID has another intent")
                return previous["node_id"]
            if agent_run_id is not None:
                from workspace.resources import ResourceDenied, ResourcePolicy
                run = uow.execute(
                    "SELECT r.status,s.status AS snapshot_status,s.subject_kind,s.subject_id "
                    "FROM runs r JOIN run_resource_snapshots s ON s.run_id=r.run_id "
                    "WHERE r.account_id=%s AND r.run_id=%s FOR UPDATE OF r",
                    (account_id, agent_run_id),
                ).fetchone()
                if run is None or run["status"] not in {"queued", "running"} or (
                    run["snapshot_status"] != "ready"
                ):
                    raise ResourceDenied("Run cannot commit a Workspace output")
                policy = ResourcePolicy(self.database)
                policy._version(uow, account_id, run["subject_kind"], run["subject_id"])
                if not policy._grants(uow, account_id, run["subject_kind"],
                                      run["subject_id"], parent_id, "create_child"):
                    raise ResourceDenied("current create_child permission is absent")
                if uow.execute(
                    "SELECT 1 FROM run_files rf JOIN stored_files sf "
                    "ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
                    "WHERE rf.account_id=%s AND rf.run_id=%s AND rf.file_id=%s "
                    "AND rf.direction='output' AND sf.source_run_id=%s",
                    (account_id, agent_run_id, file_id, agent_run_id),
                ).fetchone() is None:
                    raise ResourceDenied("file is not this Run's published output")
            file = uow.execute(
                "SELECT purpose,conversation_id,expires_at FROM stored_files "
                "WHERE account_id=%s AND file_id=%s AND status='ready' FOR UPDATE",
                (account_id, file_id),
            ).fetchone()
            if file is None:
                raise WorkspaceNotFound("file is not ready in this account")
            if file["purpose"] == "input" and file["conversation_id"] is None:
                raise WorkspaceConflict("upload source is unavailable")
            if file["purpose"] == "output" and uow.execute(
                "SELECT 1 FROM run_files WHERE account_id=%s AND file_id=%s "
                "AND direction='output'", (account_id, file_id),
            ).fetchone() is None:
                raise WorkspaceNotFound("output is not published")
            node_id = uuid7()
            uow.execute(
                "INSERT INTO workspace_nodes(node_id,account_id,workspace_id,parent_id,"
                "kind,name,name_key,file_id) VALUES (%s,%s,%s,%s,'file',%s,%s,%s)",
                (node_id, account_id, workspace_id, parent_id, display, key, file_id),
            )
            uow.execute(
                "INSERT INTO workspace_save_operations(account_id,operation_id,"
                "intent_sha256,node_id,source_kind,source_run_id) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (account_id, operation_id, intent, node_id,
                 "task_auto" if agent_run_id else "user_manual", agent_run_id),
            )
            if file["purpose"] == "input" and file["expires_at"] is not None:
                uow.execute(
                    "UPDATE stored_files SET expires_at=NULL WHERE account_id=%s AND file_id=%s",
                    (account_id, file_id),
                )
            return node_id

    @retryable_transaction
    def upgrade_file(self, account_id: UUID, node_id: UUID) -> dict[str, Any]:
        """Promote exactly one immutable entry without copying its initial object."""
        with UnitOfWork(self.database) as uow:
            self._workspace(uow, account_id)
            node = uow.execute(
                "SELECT file_id,destination_id FROM workspace_nodes WHERE account_id=%s "
                "AND node_id=%s AND kind='file' AND deleted_at IS NULL FOR UPDATE",
                (account_id, node_id),
            ).fetchone()
            if node is None:
                raise WorkspaceNotFound()
            if node["destination_id"] is not None:
                return self._current_in_uow(uow, account_id, node_id)
            file = uow.execute(
                "SELECT sha256 FROM stored_files WHERE account_id=%s AND file_id=%s "
                "AND status='ready' FOR UPDATE", (account_id, node["file_id"]),
            ).fetchone()
            if file is None:
                raise WorkspaceNotFound("initial file is unavailable")
            destination_id = uuid7()
            initial_operation = f"upgrade:{node_id}"
            uow.execute(
                "INSERT INTO persistent_file_destinations(destination_id,account_id,"
                "current_revision,current_file_id,current_sha256) "
                "VALUES (%s,%s,1,%s,%s)",
                (destination_id, account_id, node["file_id"], file["sha256"]),
            )
            uow.execute(
                "INSERT INTO persistent_file_revisions(account_id,destination_id,revision,"
                "file_id,sha256,operation_id) VALUES (%s,%s,1,%s,%s,%s)",
                (account_id, destination_id, node["file_id"], file["sha256"],
                 initial_operation),
            )
            uow.execute(
                "UPDATE workspace_nodes SET file_id=NULL,destination_id=%s,updated_at=now() "
                "WHERE account_id=%s AND node_id=%s",
                (destination_id, account_id, node_id),
            )
            return {"node_id": str(node_id), "destination_id": str(destination_id),
                    "revision": 1, "file_id": str(node["file_id"]),
                    "sha256": file["sha256"]}

    @staticmethod
    def _current_in_uow(uow: UnitOfWork, account_id: UUID,
                        node_id: UUID) -> dict[str, Any]:
        row = uow.execute(
            "SELECT n.destination_id,d.current_revision,d.current_file_id,"
            "d.current_sha256,n.file_id,sf.sha256 AS direct_sha256 "
            "FROM workspace_nodes n LEFT JOIN persistent_file_destinations d "
            "ON d.account_id=n.account_id AND d.destination_id=n.destination_id "
            "LEFT JOIN stored_files sf ON sf.account_id=n.account_id "
            "AND sf.file_id=n.file_id WHERE n.account_id=%s AND n.node_id=%s "
            "AND n.kind='file' AND n.deleted_at IS NULL",
            (account_id, node_id),
        ).fetchone()
        if row is None:
            raise WorkspaceNotFound()
        return {"node_id": str(node_id),
                "destination_id": str(row["destination_id"])
                    if row["destination_id"] else None,
                "revision": row["current_revision"],
                "file_id": str(row["current_file_id"] or row["file_id"]),
                "sha256": row["current_sha256"] or row["direct_sha256"]}

    def versions(self, account_id: UUID, node_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            current = self._current_in_uow(uow, account_id, node_id)
            if current["destination_id"] is None:
                return {"current": current, "revisions": []}
            rows = uow.execute(
                "SELECT r.revision,r.file_id,r.sha256,r.operation_id,r.created_at,"
                "sf.purpose,sf.conversation_id,sf.source_run_id "
                "FROM persistent_file_revisions r JOIN stored_files sf "
                "ON sf.account_id=r.account_id AND sf.file_id=r.file_id "
                "WHERE r.account_id=%s AND r.destination_id=%s ORDER BY r.revision DESC",
                (account_id, current["destination_id"]),
            ).fetchall()
            return {"current": current, "revisions": [
                {"revision": r["revision"], "file_id": str(r["file_id"]),
                 "sha256": r["sha256"], "operation_id": r["operation_id"],
                 "created_at": r["created_at"].isoformat(),
                 "source": {"purpose": r["purpose"],
                            "conversation_id": str(r["conversation_id"])
                                if r["conversation_id"] else None,
                            "run_id": str(r["source_run_id"])
                                if r["source_run_id"] else None}}
                for r in rows]}

    @retryable_transaction
    def update_file(self, account_id: UUID, node_id: UUID, run_id: UUID,
                    file_id: UUID, expected_revision: int, expected_sha256: str,
                    operation_id: str, *, task_auto: bool = False) -> dict[str, Any]:
        """Register an already published Run output with one historical CAS result."""
        if not operation_id or len(operation_id) > 200:
            raise WorkspaceConflict("invalid operation ID")
        intent = hashlib.sha256(json.dumps(
            [str(node_id), str(run_id), str(file_id), expected_revision,
             expected_sha256], separators=(",", ":")
        ).encode()).hexdigest()
        with UnitOfWork(self.database) as uow:
            self._workspace(uow, account_id)
            prior = uow.execute(
                "SELECT intent_sha256,node_id,destination_id,revision,file_id "
                "FROM workspace_version_operations WHERE account_id=%s AND operation_id=%s",
                (account_id, operation_id),
            ).fetchone()
            if prior:
                if prior["intent_sha256"] != intent:
                    raise WorkspaceConflict("operation ID has another intent")
                return {"node_id": str(prior["node_id"]),
                        "destination_id": str(prior["destination_id"]),
                        "revision": prior["revision"], "file_id": str(prior["file_id"])}
            node = uow.execute(
                "SELECT destination_id FROM workspace_nodes WHERE account_id=%s "
                "AND node_id=%s AND kind='file' AND deleted_at IS NULL FOR UPDATE",
                (account_id, node_id),
            ).fetchone()
            if node is None or node["destination_id"] is None:
                raise WorkspaceNotFound("versioned entry is unavailable")
            destination_id = node["destination_id"]
            current = self._current_in_uow(uow, account_id, node_id)
            if (current["revision"] != expected_revision or
                    current["sha256"] != expected_sha256):
                raise WorkspaceVersionConflict(current)
            from workspace.resources import ResourceDenied, ResourcePolicy
            run = uow.execute(
                "SELECT r.status,s.status AS snapshot_status,s.subject_kind,s.subject_id "
                "FROM runs r JOIN run_resource_snapshots s ON s.run_id=r.run_id "
                "WHERE r.account_id=%s AND r.run_id=%s FOR UPDATE OF r",
                (account_id, run_id),
            ).fetchone()
            if run is None or run["status"] not in {"queued", "running", "completed"} or (
                    run["snapshot_status"] != "ready"):
                raise ResourceDenied("Run cannot commit a Workspace revision")
            policy = ResourcePolicy(self.database)
            policy._version(uow, account_id, run["subject_kind"], run["subject_id"])
            if not policy._grants(uow, account_id, run["subject_kind"],
                                  run["subject_id"], node_id, "update_content"):
                raise ResourceDenied("current update_content permission is absent")
            fixed = uow.execute(
                "SELECT 1 FROM run_resource_access a JOIN run_resource_candidates c "
                "ON c.run_id=a.run_id AND c.node_id=a.node_id "
                "WHERE a.account_id=%s AND a.run_id=%s AND a.node_id=%s "
                "AND a.file_id=%s AND c.fixed_file_id=%s AND c.fixed_revision=%s",
                (account_id, run_id, node_id, current["file_id"],
                 current["file_id"], expected_revision),
            ).fetchone()
            if fixed is None:
                raise ResourceDenied("Run did not fix the expected base revision")
            published = uow.execute(
                "SELECT sf.sha256 FROM run_files rf JOIN stored_files sf "
                "ON sf.account_id=rf.account_id AND sf.file_id=rf.file_id "
                "WHERE rf.account_id=%s AND rf.run_id=%s AND rf.file_id=%s "
                "AND rf.direction='output' AND sf.source_run_id=%s "
                "AND sf.status='ready' FOR UPDATE OF sf",
                (account_id, run_id, file_id, run_id),
            ).fetchone()
            if published is None:
                raise ResourceDenied("file is not this Run's published output")
            revision = expected_revision + 1
            uow.execute(
                "INSERT INTO persistent_file_revisions(account_id,destination_id,revision,"
                "file_id,sha256,operation_id) VALUES (%s,%s,%s,%s,%s,%s)",
                (account_id, destination_id, revision, file_id,
                 published["sha256"], operation_id),
            )
            uow.execute(
                "UPDATE persistent_file_destinations SET current_revision=%s,"
                "current_file_id=%s,current_sha256=%s,updated_at=now() "
                "WHERE account_id=%s AND destination_id=%s",
                (revision, file_id, published["sha256"],
                 account_id, destination_id),
            )
            uow.execute(
                "INSERT INTO workspace_version_operations(account_id,operation_id,"
                "intent_sha256,node_id,destination_id,revision,file_id,source_kind,"
                "source_run_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (account_id, operation_id, intent, node_id, destination_id,
                 revision, file_id, "task_auto" if task_auto else "user_manual",
                 run_id if task_auto else None),
            )
            return {"node_id": str(node_id), "destination_id": str(destination_id),
                    "revision": revision, "file_id": str(file_id)}

    @staticmethod
    def _preview_token(uow: UnitOfWork, account_id: UUID, topology_version: int) -> str:
        versions = uow.execute(
            "SELECT subject_kind,subject_id,version FROM resource_policy_versions "
            "WHERE account_id=%s ORDER BY subject_kind,subject_id", (account_id,),
        ).fetchall()
        payload = [topology_version, [(row["subject_kind"], str(row["subject_id"]),
                                      row["version"]) for row in versions]]
        return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()

    def preview_change(self, account_id: UUID, node_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            workspace = uow.execute(
                "SELECT workspace_id,topology_version FROM account_workspaces "
                "WHERE account_id=%s", (account_id,),
            ).fetchone()
            if workspace is None or uow.execute(
                "SELECT 1 FROM workspace_nodes WHERE account_id=%s AND node_id=%s "
                "AND deleted_at IS NULL AND parent_id IS NOT NULL",
                (account_id, node_id),
            ).fetchone() is None:
                raise WorkspaceNotFound()
            impacted = uow.execute(
                "WITH RECURSIVE descendants AS ("
                "SELECT node_id FROM workspace_nodes WHERE node_id=%s "
                "UNION ALL SELECT n.node_id FROM workspace_nodes n "
                "JOIN descendants d ON n.parent_id=d.node_id WHERE n.deleted_at IS NULL) "
                "SELECT DISTINCT a.run_id FROM run_resource_access a "
                "JOIN descendants d ON d.node_id=a.node_id JOIN runs r ON r.run_id=a.run_id "
                "WHERE a.account_id=%s AND r.status IN ('queued','running','cancelling')",
                (node_id, account_id),
            ).fetchall()
            return {"preview_token": self._preview_token(
                uow, account_id, workspace["topology_version"]
            ), "potentially_affected_runs": [str(row["run_id"]) for row in impacted]}

    @retryable_transaction
    def move(self, account_id: UUID, node_id: UUID, parent_id: UUID, name: str,
             preview_token: str | None = None) -> None:
        display, key = normalize_name(name)
        with UnitOfWork(self.database) as uow:
            workspace_id = self._workspace(uow, account_id)
            if preview_token is not None:
                version = uow.execute(
                    "SELECT topology_version FROM account_workspaces WHERE account_id=%s",
                    (account_id,),
                ).fetchone()["topology_version"]
                if self._preview_token(uow, account_id, version) != preview_token:
                    raise WorkspaceConflict("Workspace impact preview is stale")
            if uow.execute(
                "UPDATE workspace_nodes SET parent_id=%s,name=%s,name_key=%s,updated_at=now() "
                "WHERE account_id=%s AND workspace_id=%s AND node_id=%s "
                "AND deleted_at IS NULL AND parent_id IS NOT NULL RETURNING node_id",
                (parent_id, display, key, account_id, workspace_id, node_id),
            ).fetchone() is None:
                raise WorkspaceNotFound()
            if self.commands is not None:
                from workspace.resources import ResourcePolicy
                ResourcePolicy(self.database).stop_invalid_in_uow(
                    uow, account_id, self.commands, str(node_id)
                )

    @retryable_transaction
    def remove(self, account_id: UUID, node_id: UUID,
               preview_token: str | None = None) -> None:
        with UnitOfWork(self.database) as uow:
            workspace_id = self._workspace(uow, account_id)
            if uow.execute(
                "SELECT 1 FROM tasks WHERE account_id=%s AND output_directory_id=%s",
                (account_id, node_id),
            ).fetchone() is not None:
                raise WorkspaceConflict("Task output directory is still bound")
            if preview_token is not None:
                version = uow.execute(
                    "SELECT topology_version FROM account_workspaces WHERE account_id=%s",
                    (account_id,),
                ).fetchone()["topology_version"]
                if self._preview_token(uow, account_id, version) != preview_token:
                    raise WorkspaceConflict("Workspace impact preview is stale")
            if uow.execute(
                "UPDATE workspace_nodes SET deleted_at=now(),updated_at=now() "
                "WHERE account_id=%s AND workspace_id=%s AND node_id=%s "
                "AND parent_id IS NOT NULL AND deleted_at IS NULL RETURNING node_id",
                (account_id, workspace_id, node_id),
            ).fetchone() is None:
                raise WorkspaceNotFound()
            if self.commands is not None:
                from workspace.resources import ResourcePolicy
                ResourcePolicy(self.database).stop_invalid_in_uow(
                    uow, account_id, self.commands, str(node_id)
                )
