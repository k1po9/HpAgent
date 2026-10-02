from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

from uuid6 import uuid7

from persistence.command_result import CommandResult
from persistence.repositories import IdempotencyRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import ConversationBusy, IdempotencyConflict, ResourceNotFound


def _digest(value: object) -> bytes:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).digest()


class ArtifactService:
    """Ownership-scoped commands and queries for Artifact resources."""

    def __init__(self, database: object):
        self.database = database
        self.idempotency = IdempotencyRepository()

    @retryable_transaction
    def create_artifact(
        self, account_id: UUID, source_message_id: UUID, key: str,
        instruction: str | None = None,
    ) -> CommandResult:
        instruction = self._instruction(instruction)
        payload = {"source_message_id": str(source_message_id), "instruction": instruction}
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "create_artifact", key, payload)
            if replay:
                return replay
            source = uow.execute(
                "SELECT * FROM messages WHERE account_id=%s AND message_id=%s FOR UPDATE",
                (account_id, source_message_id),
            ).fetchone()
            if not source:
                raise ResourceNotFound()
            if (source["role"] != "assistant" or source["status"] != "completed"
                    or not str(source["content"] or "").strip()):
                raise ValueError("artifact_source_invalid")
            artifact_id, version_id, event_id = uuid7(), uuid7(), uuid7()
            title = self._title(str(source["content"]))
            uow.execute(
                "INSERT INTO artifacts(artifact_id,account_id,conversation_id,"
                "source_message_id,title) VALUES (%s,%s,%s,%s,%s)",
                (artifact_id, account_id, source["conversation_id"], source_message_id, title),
            )
            uow.execute(
                "INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,"
                "version,instruction,source_markdown) VALUES (%s,%s,%s,1,%s,%s)",
                (version_id, artifact_id, account_id, instruction, source["content"]),
            )
            self._enqueue(
                uow, event_id, account_id, source["conversation_id"], artifact_id, version_id, 'create_artifact', key
            )
            body = self._detail(uow, account_id, artifact_id)
            self._complete(uow, account_id, "create_artifact", key, 202, body)
            return CommandResult(202, body)

    @retryable_transaction
    def create_version(
        self, account_id: UUID, artifact_id: UUID, key: str, instruction: str,
    ) -> CommandResult:
        instruction = cast(str, self._instruction(instruction, required=True))
        payload = {"artifact_id": str(artifact_id), "instruction": instruction}
        with UnitOfWork(self.database) as uow:
            replay = self._claim(uow, account_id, "create_artifact_version", key, payload)
            if replay:
                return replay
            artifact = uow.execute(
                "SELECT * FROM artifacts WHERE account_id=%s AND artifact_id=%s FOR UPDATE",
                (account_id, artifact_id),
            ).fetchone()
            if not artifact:
                raise ResourceNotFound()
            latest = uow.execute(
                "SELECT artifact_version_id,version,source_markdown FROM artifact_versions "
                "WHERE account_id=%s AND artifact_id=%s ORDER BY version DESC LIMIT 1",
                (account_id, artifact_id),
            ).fetchone()
            parent = uow.execute(
                "SELECT artifact_version_id FROM artifact_versions WHERE account_id=%s "
                "AND artifact_id=%s AND status='completed' ORDER BY version DESC LIMIT 1",
                (account_id, artifact_id),
            ).fetchone()
            version_id, event_id = uuid7(), uuid7()
            number = int(latest["version"]) + 1 if latest else 1
            uow.execute(
                "INSERT INTO artifact_versions(artifact_version_id,artifact_id,account_id,"
                "version,parent_version_id,instruction,source_markdown) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (version_id, artifact_id, account_id, number,
                 parent["artifact_version_id"] if parent else None, instruction, latest["source_markdown"] if latest else None),
            )
            self._enqueue(
                uow, event_id, account_id, artifact["conversation_id"], artifact_id, version_id, 'create_artifact_version', key
            )
            version = self._version_row(uow, account_id, version_id)
            body = {"artifact": self._artifact_dto(artifact), "version": self._version_dto(version)}
            self._complete(uow, account_id, "create_artifact_version", key, 202, body)
            return CommandResult(202, body)

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
                "ORDER BY created_at,artifact_id", (account_id, message_id),
            ).fetchall()
            items = []
            for row in rows:
                latest = uow.execute(
                    "SELECT * FROM artifact_versions WHERE account_id=%s AND artifact_id=%s "
                    "ORDER BY version DESC LIMIT 1", (account_id, row["artifact_id"]),
                ).fetchone()
                items.append({"artifact": self._artifact_dto(row),
                              "latest_version": self._version_dto(latest) if latest else None})
            return {"items": items}

    def get_artifact(self, account_id: UUID, artifact_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            return self._detail(uow, account_id, artifact_id, latest_key="latest_version")

    def list_versions(self, account_id: UUID, artifact_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            artifact = self._artifact_row(uow, account_id, artifact_id)
            rows = uow.execute(
                "SELECT * FROM artifact_versions WHERE account_id=%s AND artifact_id=%s "
                "ORDER BY version", (account_id, artifact_id),
            ).fetchall()
            return {"artifact": self._artifact_dto(artifact),
                    "items": [self._version_dto(row) for row in rows]}

    def get_version(self, account_id: UUID, version_id: UUID) -> dict[str, Any]:
        with UnitOfWork(self.database) as uow:
            row = self._version_row(uow, account_id, version_id)
            return {"version": self._version_dto(row)}

    def _detail(self, uow: UnitOfWork, account_id: UUID, artifact_id: UUID,
                latest_key: str = "version") -> dict[str, Any]:
        artifact = self._artifact_row(uow, account_id, artifact_id)
        latest = uow.execute(
            "SELECT * FROM artifact_versions WHERE account_id=%s AND artifact_id=%s "
            "ORDER BY version DESC LIMIT 1", (account_id, artifact_id),
        ).fetchone()
        return {"artifact": self._artifact_dto(artifact),
                latest_key: self._version_dto(latest) if latest else None}

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
            "SELECT * FROM artifact_versions WHERE account_id=%s AND artifact_version_id=%s",
            (account_id, version_id),
        ).fetchone()
        if not row:
            raise ResourceNotFound()
        return row

    def _claim(self, uow: UnitOfWork, account_id: UUID, operation: str,
               key: str, payload: object) -> CommandResult | None:
        digest = _digest(payload)
        row = self.idempotency.claim(
            uow, uuid7(), account_id, operation, key, digest,
            datetime.now(UTC) + timedelta(days=7),
        )
        if row is None:
            return None
        if bytes(row["request_hash"]) != digest:
            raise IdempotencyConflict()
        if row["status"] == "completed":
            return CommandResult(int(row["response_status"]),
                                 cast(dict[str, Any], row["response_body"]), replayed=True)
        raise ConversationBusy()

    def _complete(self, uow: UnitOfWork, account_id: UUID, operation: str,
                  key: str, status: int, body: dict[str, Any]) -> None:
        self.idempotency.complete(
            uow, account_id, operation, key, status, json.dumps(body)
        )

    @staticmethod
    def _enqueue(uow, event_id, account_id, conversation_id, artifact_id, version_id, operation, key):
        from resources.work_budget import WorkBudgetService
        from work_domain.models import Requirement, continuation
        from work_domain.persistence import WorkRepository, sync_schedule
        work_id = uuid7()
        # A new background mandate owns this build and its cost; source is merely input.
        command = uow.execute("SELECT idempotency_command_id FROM idempotency_commands WHERE account_id=%s AND operation=%s AND idempotency_key=%s AND status='in_progress'", (account_id,operation,key)).fetchone()
        if command is None:
            raise ValueError('artifact build command missing')
        value = Requirement('Build the requested HTML artifact', 'artifact_build',
                            {'schema_version':1,'artifact_version_id':str(version_id)},
                            acceptance_criteria=({'id':'artifact_ready','required':True,'evidence_types':['artifact_version']},)).to_dict()
        uow.execute("INSERT INTO works(work_id,account_id,title,continuation) VALUES (%s,%s,'HTML artifact build',%s::jsonb)",
                    (work_id,account_id,json.dumps(continuation())))
        WorkBudgetService.create(uow,account_id,work_id)
        WorkRepository.insert_requirement(uow,account_id,work_id,1,value,command['idempotency_command_id'],'artifact_build_requested')
        work = WorkRepository.get(uow,account_id,work_id)
        if conversation_id:
            from work_domain.commands import WorkCommandService
            WorkCommandService._link(uow,work,conversation_id,command['idempotency_command_id'])
        WorkRepository.event(uow,work,'accepted',command_id=command['idempotency_command_id'])
        sync_schedule(uow,work,value)
        WorkRepository.wakeup(uow,work,f'artifact:{version_id}','accepted','artifact_build_requested')

    @staticmethod
    def _instruction(value: str | None, required: bool = False) -> str | None:
        result = value.strip() if value else None
        if required and not result:
            raise ValueError("artifact_instruction_required")
        if result and len(result) > 4000:
            raise ValueError("artifact_instruction_too_long")
        return result

    @staticmethod
    def _title(markdown: str) -> str:
        for line in markdown.splitlines():
            value = line.lstrip("# ").strip()
            if value:
                return value[:200]
        return "Web Artifact"

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return (value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
                if value else None)

    @classmethod
    def _artifact_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        return {"artifact_id": str(row["artifact_id"]),
                "conversation_id": (str(row["conversation_id"])
                                    if row["conversation_id"] is not None else None),
                "source_message_id": (str(row["source_message_id"])
                                      if row["source_message_id"] is not None else None),
                "kind": row["kind"], "title": row["title"],
                "created_at": cls._timestamp(row["created_at"]),
                "updated_at": cls._timestamp(row["updated_at"])}

    @classmethod
    def _version_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        failure = None
        if row["status"] == "failed":
            failure = {"code": row["failure_code"],
                       "message": row["failure_message"] or "Artifact 生成失败。"}
        return {"artifact_version_id": str(row["artifact_version_id"]),
                "artifact_id": str(row["artifact_id"]), "version": row["version"],
                "parent_version_id": str(row["parent_version_id"]) if row["parent_version_id"] else None,
                "status": row["status"], "instruction": row["instruction"],
                "html": row["html"], "failure": failure,
                "producing_run_id": str(row['producing_run_id']) if row.get('producing_run_id') else None,
                "producing_execution_id": str(row['producing_execution_id']) if row.get('producing_execution_id') else None,
                "producing_operation_id": row.get('producing_operation_id'),
                "created_at": cls._timestamp(row["created_at"]),
                "started_at": cls._timestamp(row["started_at"]),
                "completed_at": cls._timestamp(row["completed_at"])}
