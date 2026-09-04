"""Durable approval grants for high-risk file actions."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast
from uuid import UUID

from uuid6 import uuid7

from persistence.repositories import IdempotencyRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import ConversationBusy, DomainError, IdempotencyConflict, ResourceNotFound
from web_domain.services import CommandResult

ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "consumed"]


class ApprovalNotPending(DomainError):
    pass


class ApprovalNotGranted(RuntimeError):
    pass


@dataclass(frozen=True)
class FileActionApproval:
    approval_id: UUID
    run_id: UUID
    operation_id: str
    tool_name: str
    action_summary: str
    arguments_hash: str
    status: ApprovalStatus
    expires_at: datetime
    intent: dict[str, Any]
    execution_id: str | None = None
    execution_fencing_token: int | None = None


def _digest(value: object) -> bytes:
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str
    ).encode()).digest()


class FileActionApprovalService:
    """Keep compact intent metadata; never persist file content or raw arguments."""

    def __init__(self, database: object) -> None:
        self.database = database
        self.idempotency = IdempotencyRepository()

    @retryable_transaction
    def request(
        self, account_id: UUID, conversation_id: UUID, run_id: UUID,
        operation_id: str, tool_name: str, action_summary: str, arguments_hash: str,
        *, intent: dict[str, Any] | None = None,
        ttl: timedelta = timedelta(hours=24),
    ) -> FileActionApproval:
        if ttl.total_seconds() <= 0 or len(arguments_hash) != 64:
            raise ValueError("invalid approval request")
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "INSERT INTO file_action_approvals(approval_id,account_id,conversation_id,"
                "run_id,operation_id,tool_name,action_summary,arguments_hash,intent,expires_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,"
                "now()+(%s * interval '1 second')) "
                "ON CONFLICT (run_id,operation_id) DO UPDATE SET updated_at=now() RETURNING *",
                (uuid7(), account_id, conversation_id, run_id, operation_id, tool_name,
                 action_summary, arguments_hash, json.dumps(intent or {}, sort_keys=True),
                 ttl.total_seconds()),
            ).fetchone()
            if (row["account_id"] != account_id or row["tool_name"] != tool_name
                    or row["arguments_hash"] != arguments_hash
                    or dict(row["intent"]) != (intent or {})):
                raise ValueError("approval intent identity mismatch")
            return self._model(row)

    def list_for_run(self, account_id: UUID, run_id: UUID) -> list[dict[str, Any]]:
        with UnitOfWork(self.database) as uow:
            rows = uow.execute(
                "SELECT * FROM file_action_approvals WHERE account_id=%s AND run_id=%s "
                "ORDER BY requested_at,approval_id", (account_id, run_id),
            ).fetchall()
            if not rows and uow.execute(
                "SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s", (account_id, run_id),
            ).fetchone() is None:
                raise ResourceNotFound()
        return [self._dto(row) for row in rows]

    @retryable_transaction
    def decide(
        self, account_id: UUID, approval_id: UUID,
        decision: Literal["approved", "rejected"], key: str,
    ) -> CommandResult:
        operation = "approve_file_action" if decision == "approved" else "reject_file_action"
        digest = _digest({"approval_id": str(approval_id), "decision": decision})
        with UnitOfWork(self.database) as uow:
            previous = self.idempotency.claim(
                uow, uuid7(), account_id, operation, key, digest,
                datetime.now(UTC) + timedelta(days=7),
            )
            if previous is not None:
                if bytes(previous["request_hash"]) != digest:
                    raise IdempotencyConflict()
                if previous["status"] == "completed":
                    return CommandResult(int(previous["response_status"]), cast(
                        dict[str, Any], previous["response_body"]), replayed=True)
                raise ConversationBusy()
            row = uow.execute(
                "SELECT * FROM file_action_approvals WHERE account_id=%s "
                "AND approval_id=%s FOR UPDATE", (account_id, approval_id),
            ).fetchone()
            if row is None:
                raise ResourceNotFound()
            if row["status"] == "pending" and row["expires_at"] <= datetime.now(UTC):
                uow.execute("UPDATE file_action_approvals SET status='expired',updated_at=now() "
                            "WHERE approval_id=%s", (approval_id,))
                raise ApprovalNotPending()
            if row["status"] != "pending":
                raise ApprovalNotPending()
            row = uow.execute(
                "UPDATE file_action_approvals SET status=%s,decided_at=now(),"
                "decided_by_account_id=%s,updated_at=now() WHERE approval_id=%s RETURNING *",
                (decision, account_id, approval_id),
            ).fetchone()
            body = {"approval": self._dto(row)}
            self.idempotency.complete(uow, account_id, operation, key, 200, json.dumps(body))
            return CommandResult(200, body)

    @retryable_transaction
    def consume(
        self, account_id: UUID, run_id: UUID, operation_id: str,
        tool_name: str, arguments_hash: str, *, execution_id: str,
        fencing_token: int,
    ) -> UUID:
        with UnitOfWork(self.database) as uow:
            row = uow.execute(
                "SELECT * FROM file_action_approvals WHERE account_id=%s AND run_id=%s "
                "AND operation_id=%s FOR UPDATE", (account_id, run_id, operation_id),
            ).fetchone()
            if (row is None or row["tool_name"] != tool_name
                    or row["arguments_hash"] != arguments_hash):
                raise ApprovalNotGranted()
            if row["status"] == "consumed":
                if (row["execution_id"] == execution_id == operation_id
                        and row["execution_fencing_token"] == fencing_token):
                    return cast(UUID, row["approval_id"])
                raise ApprovalNotGranted()
            if (row["status"] != "approved" or row["expires_at"] <= datetime.now(UTC)
                    or execution_id != operation_id or fencing_token < 1):
                raise ApprovalNotGranted()
            uow.execute(
                "UPDATE file_action_approvals SET status='consumed',consumed_at=now(),"
                "execution_id=%s,execution_fencing_token=%s,execution_bound_at=now(),"
                "updated_at=now() WHERE approval_id=%s",
                (execution_id, fencing_token, row["approval_id"]),
            )
            return cast(UUID, row["approval_id"])

    @staticmethod
    def _model(row: dict[str, Any]) -> FileActionApproval:
        return FileActionApproval(
            row["approval_id"], row["run_id"], row["operation_id"], row["tool_name"],
            row["action_summary"], row["arguments_hash"], row["status"], row["expires_at"],
            dict(row.get("intent") or {}), row.get("execution_id"),
            row.get("execution_fencing_token"),
        )

    @staticmethod
    def _dto(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "approval_id": str(row["approval_id"]), "run_id": str(row["run_id"]),
            "operation_id": row["operation_id"], "tool_name": row["tool_name"],
            "action_summary": row["action_summary"], "status": row["status"],
            "requested_at": row["requested_at"].isoformat(),
            "expires_at": row["expires_at"].isoformat(),
            "decided_at": row["decided_at"].isoformat() if row["decided_at"] else None,
            "consumed_at": row["consumed_at"].isoformat() if row["consumed_at"] else None,
        }
