"""Existing Chat responses and shared Run cancellation snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from persistence.messages import MessageRepository
from persistence.runs import RunRepository
from persistence.uow import UnitOfWork
from web_domain.failures import is_failure_retryable
from web_domain.run_usage_projection import load_run_budget_projection


class ChatProjection:
    @classmethod
    def send_result(cls, uow: UnitOfWork, run_id: UUID) -> dict[str, Any]:
        snapshot = cls.snapshot_for_run(uow, run_id)
        user = MessageRepository().user_for_run(uow, run_id)
        run = snapshot["run"]
        user_dto = cls._message_dto(user)
        user_dto["files"] = cls._message_file_dtos(uow, user["message_id"])
        return {
            "message_id": user and str(user["message_id"]),
            "run_id": str(run_id),
            "session_id": run["session_id"],
            "user_message": user_dto,
            "assistant_message": snapshot["assistant_message"],
            "run": run,
        }

    @classmethod
    def retry_result(cls, uow: UnitOfWork, source_run_id: UUID, run_id: UUID) -> dict[str, Any]:
        snapshot = cls.snapshot_for_run(uow, run_id)
        return {
            "run_id": str(run_id),
            "source_run_id": str(source_run_id),
            "retry_of_run_id": str(source_run_id),
            "assistant_message": snapshot["assistant_message"],
            "run": snapshot["run"],
        }

    @classmethod
    def snapshot_for_run(cls, uow: UnitOfWork, run_id: UUID) -> dict[str, Any]:
        run = RunRepository().get_in_uow(uow, run_id)
        if run["source_kind"] == "work":
            from work_domain.persistence import dto

            value = dto(run)
            value["budget"] = load_run_budget_projection(uow, run_id)
            value["execution_id"] = str(RunRepository().root_execution_id(uow, run_id))
            return {"source_kind": "work", "run": value}
        message = MessageRepository().assistant_for_run(uow, run_id)
        run_dto = cls._run_dto(run)
        run_dto["budget"] = load_run_budget_projection(uow, run_id)
        message_dto = cls._message_dto(message)
        message_dto["files"] = cls._message_file_dtos(uow, message["message_id"])
        run_dto["execution_id"] = str(RunRepository().root_execution_id(uow, run_id))
        return {"source_kind": "chat", "run": run_dto, "assistant_message": message_dto}

    @staticmethod
    def _timestamp(value: datetime | None) -> str | None:
        return (
            value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
            if value
            else None
        )

    @classmethod
    def conversation_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "conversation_id": str(row["conversation_id"]),
            "title": row["title"],
            "status": row["status"],
            "last_message_seq": row["last_message_seq"],
            "metadata_version": row["metadata_version"],
            "created_at": cls._timestamp(row["created_at"]),
            "updated_at": cls._timestamp(row["updated_at"]),
        }

    @classmethod
    def _message_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "message_id": str(row["message_id"]),
            "conversation_id": str(row["conversation_id"]),
            "role": row["role"],
            "status": row["status"],
            "content": row["content"],
            "sequence": row["sequence"],
            "client_request_id": str(row["client_request_id"])
            if row["client_request_id"]
            else None,
            "produced_by_run_id": str(row["produced_by_run_id"])
            if row["produced_by_run_id"]
            else None,
            "created_at": cls._timestamp(row["created_at"]),
            "completed_at": cls._timestamp(row["completed_at"]),
        }

    @classmethod
    def _run_dto(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        failure = None
        if row["status"] == "failed":
            failure = {
                "code": row["failure_code"],
                "message": row["failure_message"] or "Agent 暂时无法完成本次请求，请稍后重试。",
                "retryable": is_failure_retryable(row["failure_code"]),
            }
        return {
            "source_kind": "chat",
            "run_id": str(row["run_id"]),
            "conversation_id": str(row["conversation_id"]),
            "session_id": str(row["session_id"]) if row["session_id"] else None,
            "trigger_message_id": str(row["trigger_message_id"]),
            "retry_of_run_id": str(row["retry_of_run_id"]) if row["retry_of_run_id"] else None,
            "agent_strategy": str(row.get("agent_strategy") or "react"),
            "status": row["status"],
            "failure": failure,
            "version": row["version"],
            "created_at": cls._timestamp(row["created_at"]),
            "started_at": cls._timestamp(row["started_at"]),
            "finished_at": cls._timestamp(row["finished_at"]),
            "updated_at": cls._timestamp(row["updated_at"]),
        }

    @staticmethod
    def _message_file_dtos(uow: UnitOfWork, message_id: UUID) -> list[dict[str, Any]]:
        rows = MessageRepository().bound_files(uow, message_id)
        return [
            {
                "file_id": str(row["file_id"]),
                "file_name": row["display_name"],
                "purpose": row["purpose"],
                "status": row["status"],
                "size_bytes": row["size_bytes"],
            }
            for row in rows
        ]
