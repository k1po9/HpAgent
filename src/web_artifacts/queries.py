from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound

_VERSION_SELECT = (
    "SELECT * FROM artifact_versions LEFT JOIN LATERAL ("
    "SELECT f.ref_id AS result_ref_id,e.invocation_id AS producing_invocation_id,"
    "cr.payload->'checks' AS checks,cr.payload->>'outcome' AS result_outcome,"
    "cr.payload->>'summary' AS result_summary FROM run_executions e "
    "JOIN capability_results cr ON cr.account_id=e.account_id "
    "AND cr.producing_execution_id=e.execution_id AND cr.invocation_id=e.invocation_id "
    "LEFT JOIN capability_result_refs f ON f.account_id=cr.account_id "
    "AND f.invocation_id=cr.invocation_id AND f.artifact_version_id=artifact_versions.artifact_version_id "
    "AND f.type='artifact_version' WHERE e.account_id=artifact_versions.account_id "
    "AND e.execution_id=artifact_versions.producing_execution_id "
    "ORDER BY f.created_at,f.ref_id LIMIT 1) result_metadata ON true "
)


class ArtifactQueries:
    """Account-scoped Artifact reads and existing response projections."""

    def __init__(self, database: object):
        self.database = database

    def list_for_message(self, account_id: UUID, message_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            source = uow.execute(
                "SELECT 1 FROM messages WHERE account_id=%s AND message_id=%s",
                (account_id, message_id),
            ).fetchone()
            if not source:
                raise ResourceNotFound()
            rows = uow.execute(
                "SELECT * FROM artifacts WHERE account_id=%s AND source_message_id=%s "
                "ORDER BY created_at,artifact_id",
                (account_id, message_id),
            ).fetchall()
            items = []
            for row in rows:
                latest = uow.execute(
                    _VERSION_SELECT + "WHERE account_id=%s AND artifact_id=%s "
                    "ORDER BY version DESC LIMIT 1",
                    (account_id, row["artifact_id"]),
                ).fetchone()
                items.append(
                    {
                        "artifact": self._artifact_dto(row),
                        "latest_version": self._version_dto(latest) if latest else None,
                    }
                )
            return {"items": items}

    def get_artifact(self, account_id: UUID, artifact_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            return self._detail(uow, account_id, artifact_id, latest_key="latest_version")

    def list_versions(self, account_id: UUID, artifact_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            artifact = self._artifact_row(uow, account_id, artifact_id)
            rows = uow.execute(
                _VERSION_SELECT + "WHERE account_id=%s AND artifact_id=%s ORDER BY version",
                (account_id, artifact_id),
            ).fetchall()
            return {
                "artifact": self._artifact_dto(artifact),
                "items": [self._version_dto(row) for row in rows],
            }

    def get_version(self, account_id: UUID, version_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = self._version_row(uow, account_id, version_id)
            return {"version": self._version_dto(row)}

    def _detail(
        self, uow: UnitOfWork, account_id: UUID, artifact_id: UUID, latest_key: str = "version"
    ) -> dict[str, Any]:
        artifact = self._artifact_row(uow, account_id, artifact_id)
        latest = uow.execute(
            _VERSION_SELECT + "WHERE account_id=%s AND artifact_id=%s "
            "ORDER BY version DESC LIMIT 1",
            (account_id, artifact_id),
        ).fetchone()
        return {
            "artifact": self._artifact_dto(artifact),
            latest_key: self._version_dto(latest) if latest else None,
        }

    @staticmethod
    def _artifact_row(uow: UnitOfWork, account_id: UUID, artifact_id: UUID):
        row = uow.execute(
            "SELECT * FROM artifacts WHERE account_id=%s AND artifact_id=%s",
            (account_id, artifact_id),
        ).fetchone()
        if not row:
            raise ResourceNotFound()
        return row

    @staticmethod
    def _version_row(uow: UnitOfWork, account_id: UUID, version_id: UUID):
        row = uow.execute(
            _VERSION_SELECT + "WHERE account_id=%s AND artifact_version_id=%s",
            (account_id, version_id),
        ).fetchone()
        if not row:
            raise ResourceNotFound()
        return row

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return (
            value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if value
            else None
        )

    @classmethod
    def _artifact_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "artifact_id": str(row["artifact_id"]),
            "conversation_id": (
                str(row["conversation_id"]) if row["conversation_id"] is not None else None
            ),
            "source_message_id": (
                str(row["source_message_id"]) if row["source_message_id"] is not None else None
            ),
            "kind": row["kind"],
            "title": row["title"],
            "created_at": cls._timestamp(row["created_at"]),
            "updated_at": cls._timestamp(row["updated_at"]),
        }

    @classmethod
    def _version_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        failure = None
        if row["status"] == "failed":
            failure = {
                "code": row["failure_code"],
                "message": row["failure_message"] or "Artifact 生成失败。",
            }
        return {
            "artifact_version_id": str(row["artifact_version_id"]),
            "artifact_id": str(row["artifact_id"]),
            "version": row["version"],
            "parent_version_id": str(row["parent_version_id"])
            if row["parent_version_id"]
            else None,
            "status": row["status"],
            "instruction": row["instruction"],
            "html": row["html"],
            "failure": failure,
            "producing_run_id": str(row["producing_run_id"])
            if row.get("producing_run_id")
            else None,
            "producing_execution_id": str(row["producing_execution_id"])
            if row.get("producing_execution_id")
            else None,
            "producing_operation_id": row.get("producing_operation_id"),
            "producing_invocation_id": row.get("producing_invocation_id"),
            "result_ref_id": str(row["result_ref_id"]) if row.get("result_ref_id") else None,
            "checks": row.get("checks") or [],
            "result_outcome": row.get("result_outcome"),
            "result_summary": row.get("result_summary"),
            "created_at": cls._timestamp(row["created_at"]),
            "started_at": cls._timestamp(row["started_at"]),
            "completed_at": cls._timestamp(row["completed_at"]),
        }
