"""PostgreSQL durable data plane for Agent workflows.

Transactions are intentionally short. No model, tool, Redis, Hindsight, or
workspace call is made while a database transaction is open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from persistence.uow import UnitOfWork, retryable_transaction


class LeaseConflict(RuntimeError):
    code = "execution_lease_conflict"


class StaleFencingToken(RuntimeError):
    code = "stale_fencing_token"


class TranscriptVersionConflict(RuntimeError):
    code = "transcript_version_conflict"


@dataclass(frozen=True)
class ExecutionLease:
    account_id: str
    run_id: str
    fencing_token: int
    lease_expires_at: str


class AgentDataStore:
    def __init__(self, database_url: object, *, lease_ttl_seconds: int = 3600):
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self.database_url = database_url
        self.lease_ttl_seconds = lease_ttl_seconds

    @retryable_transaction
    def acquire_lease(self, account_id: str, run_id: str) -> ExecutionLease:
        account_uuid, run_uuid = UUID(account_id), UUID(run_id)
        with UnitOfWork(self.database_url) as uow:
            uow.execute(
                "INSERT INTO account_execution_leases(account_id) VALUES (%s) "
                "ON CONFLICT (account_id) DO NOTHING",
                (account_uuid,),
            )
            row = uow.execute(
                "SELECT * FROM account_execution_leases WHERE account_id=%s FOR UPDATE",
                (account_uuid,),
            ).fetchone()
            if (
                row["owner_run_id"] is not None
                and str(row["owner_run_id"]) != run_id
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] > uow.execute("SELECT now() AS now").fetchone()["now"]
            ):
                raise LeaseConflict(f"account lease owned by run {row['owner_run_id']}")
            same_owner = str(row["owner_run_id"]) == run_id
            token = int(row["fencing_token"]) if same_owner else int(row["fencing_token"]) + 1
            expires = uow.execute(
                "UPDATE account_execution_leases SET owner_run_id=%s,fencing_token=%s,"
                "lease_expires_at=now()+(%s * interval '1 second'),updated_at=now() "
                "WHERE account_id=%s RETURNING lease_expires_at",
                (run_uuid, token, self.lease_ttl_seconds, account_uuid),
            ).fetchone()["lease_expires_at"]
        return ExecutionLease(account_id, run_id, token, expires.isoformat())

    @retryable_transaction
    def validate_and_renew_lease(
        self, account_id: str, run_id: str, fencing_token: int
    ) -> ExecutionLease:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "UPDATE account_execution_leases SET "
                "lease_expires_at=now()+(%s * interval '1 second'),updated_at=now() "
                "WHERE account_id=%s AND owner_run_id=%s AND fencing_token=%s "
                "AND lease_expires_at>now() RETURNING lease_expires_at",
                (self.lease_ttl_seconds, UUID(account_id), UUID(run_id), fencing_token),
            ).fetchone()
            if row is None:
                raise StaleFencingToken("execution lease is absent, expired, or fenced")
        return ExecutionLease(account_id, run_id, fencing_token, row["lease_expires_at"].isoformat())

    @retryable_transaction
    def release_lease(self, account_id: str, run_id: str, fencing_token: int) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "UPDATE account_execution_leases SET owner_run_id=NULL,lease_expires_at=NULL,"
                "updated_at=now() WHERE account_id=%s AND owner_run_id=%s "
                "AND fencing_token=%s RETURNING account_id",
                (UUID(account_id), UUID(run_id), fencing_token),
            ).fetchone()
            return row is not None

    def run_identity(self, run_id: str) -> dict[str, Any]:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT run_id,account_id,conversation_id,session_id,trigger_message_id,"
                "agent_strategy,status FROM runs WHERE run_id=%s",
                (UUID(run_id),),
            ).fetchone()
        if row is None:
            raise ValueError("run not found")
        return {key: str(value) if isinstance(value, UUID) else value for key, value in row.items()}

    def operation_result(self, operation_id: str) -> dict[str, Any] | None:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT result_payload FROM agent_operations "
                "WHERE operation_id=%s AND status='completed'",
                (operation_id,),
            ).fetchone()
        return dict(row["result_payload"]) if row else None

    def tool_call_arguments(
        self, arguments_ref: str, tool_call_id: str
    ) -> dict[str, Any]:
        decision_ref, separator, referenced_call_id = arguments_ref.rpartition("#")
        if not separator or referenced_call_id != tool_call_id:
            raise ValueError("invalid tool arguments reference")
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT result_payload FROM agent_operations "
                "WHERE result_ref=%s AND status='completed'",
                (decision_ref,),
            ).fetchone()
        if row is None:
            raise ValueError("model decision reference not found")
        arguments_by_call = dict(row["result_payload"]).get(
            "tool_call_arguments", {}
        )
        arguments = arguments_by_call.get(tool_call_id)
        if not isinstance(arguments, dict):
            raise ValueError("tool call arguments not found")
        return dict(arguments)

    @retryable_transaction
    def begin_operation(self, operation_id: str, run_id: str, operation_type: str) -> dict[str, Any] | None:
        """Return the prior compact result when completed, else mark an attempt."""
        with UnitOfWork(self.database_url) as uow:
            inserted = uow.execute(
                "INSERT INTO agent_operations(operation_id,run_id,operation_type) "
                "VALUES (%s,%s,%s) ON CONFLICT (operation_id) DO NOTHING "
                "RETURNING operation_id",
                (operation_id, UUID(run_id), operation_type),
            ).fetchone()
            if inserted is not None:
                return None
            row = uow.execute(
                "SELECT run_id,operation_type,status,result_payload FROM agent_operations "
                "WHERE operation_id=%s FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if str(row["run_id"]) != run_id or row["operation_type"] != operation_type:
                raise ValueError("operation_id identity mismatch")
            if row["status"] == "completed":
                return dict(row["result_payload"])
            uow.execute(
                "UPDATE agent_operations SET status='started',error_code=NULL,"
                "attempt_count=attempt_count+1,updated_at=now() WHERE operation_id=%s",
                (operation_id,),
            )
        return None

    @retryable_transaction
    def fail_operation(self, operation_id: str, error_code: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            uow.execute(
                "UPDATE agent_operations SET status='failed',error_code=%s,updated_at=now() "
                "WHERE operation_id=%s AND status<>'completed'",
                (error_code, operation_id),
            )

    @retryable_transaction
    def record_operation_intent(
        self, operation_id: str, intent: dict[str, Any]
    ) -> None:
        """Persist compact side-effect intent before invoking an external tool."""
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "UPDATE agent_operations SET result_payload=%s,updated_at=now() "
                "WHERE operation_id=%s AND status='started' RETURNING operation_id",
                (Jsonb(intent), operation_id),
            ).fetchone()
            if row is None:
                raise ValueError("tool intent operation is not active")

    @retryable_transaction
    def create_transcript(
        self,
        *,
        transcript_id: str,
        run_id: str,
        account_id: str,
        conversation_id: str,
        session_id: str,
        messages: list[dict[str, Any]],
        operation_id: str,
    ) -> int:
        with UnitOfWork(self.database_url) as uow:
            uow.execute(
                "INSERT INTO agent_transcripts(transcript_id,run_id,account_id,conversation_id,session_id) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
                (
                    transcript_id,
                    UUID(run_id),
                    UUID(account_id),
                    UUID(conversation_id),
                    UUID(session_id),
                ),
            )
            transcript = uow.execute(
                "SELECT transcript_id,version FROM agent_transcripts WHERE run_id=%s FOR UPDATE",
                (UUID(run_id),),
            ).fetchone()
            transcript_id = str(transcript["transcript_id"])
            if int(transcript["version"]) == 0:
                uow.execute(
                    "INSERT INTO agent_transcript_events(transcript_id,sequence,event_type,"
                    "operation_id,payload) VALUES (%s,1,'context',%s,%s)",
                    (transcript_id, operation_id, Jsonb({"messages": messages})),
                )
                uow.execute(
                    "UPDATE agent_transcripts SET version=1,updated_at=now() "
                    "WHERE transcript_id=%s",
                    (transcript_id,),
                )
            payload = {
                "schema_version": 1,
                "transcript_id": transcript_id,
                "transcript_version": 1,
                "context_ref": f"transcript:{transcript_id}:1",
            }
            self._complete_operation(
                uow, operation_id, str(payload["context_ref"]), payload
            )
        return 1

    def load_messages(self, transcript_id: str) -> tuple[list[dict[str, Any]], int]:
        with UnitOfWork(self.database_url) as uow:
            transcript = uow.execute(
                "SELECT version FROM agent_transcripts WHERE transcript_id=%s",
                (transcript_id,),
            ).fetchone()
            if transcript is None:
                raise ValueError("transcript not found")
            rows = uow.execute(
                "SELECT event_type,payload FROM agent_transcript_events "
                "WHERE transcript_id=%s ORDER BY sequence",
                (transcript_id,),
            ).fetchall()
        messages: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row["payload"])
            if row["event_type"] == "context":
                messages.extend(dict(item) for item in payload.get("messages", []))
            elif row["event_type"] in {"model_decision", "tool_result", "system"}:
                message = payload.get("message")
                if isinstance(message, dict):
                    messages.append(dict(message))
        return messages, int(transcript["version"])

    @retryable_transaction
    def complete_operation_with_event(
        self,
        *,
        transcript_id: str,
        expected_version: int,
        event_type: str,
        operation_id: str,
        event_payload: dict[str, Any],
        result_ref: str,
        result_payload: dict[str, Any],
    ) -> int:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT version FROM agent_transcripts WHERE transcript_id=%s FOR UPDATE",
                (transcript_id,),
            ).fetchone()
            if row is None:
                raise ValueError("transcript not found")
            current = int(row["version"])
            if current != expected_version:
                existing = uow.execute(
                    "SELECT sequence FROM agent_transcript_events "
                    "WHERE transcript_id=%s AND operation_id=%s",
                    (transcript_id, operation_id),
                ).fetchone()
                if existing is not None:
                    return int(existing["sequence"])
                raise TranscriptVersionConflict(
                    f"expected transcript version {expected_version}, found {current}"
                )
            version = current + 1
            uow.execute(
                "INSERT INTO agent_transcript_events(transcript_id,sequence,event_type,"
                "operation_id,payload) VALUES (%s,%s,%s,%s,%s)",
                (transcript_id, version, event_type, operation_id, Jsonb(event_payload)),
            )
            uow.execute(
                "UPDATE agent_transcripts SET version=%s,updated_at=now() "
                "WHERE transcript_id=%s",
                (version, transcript_id),
            )
            result_payload["transcript_version"] = version
            self._complete_operation(uow, operation_id, result_ref, result_payload)
        return version

    @retryable_transaction
    def complete_operation(
        self, operation_id: str, result_ref: str, result_payload: dict[str, Any]
    ) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._complete_operation(uow, operation_id, result_ref, result_payload)

    @staticmethod
    def _complete_operation(
        uow: UnitOfWork, operation_id: str, result_ref: str, result_payload: dict[str, Any]
    ) -> None:
        uow.execute(
            "UPDATE agent_operations SET status='completed',result_ref=%s,result_payload=%s,"
            "error_code=NULL,completed_at=now(),updated_at=now() WHERE operation_id=%s",
            (result_ref, Jsonb(result_payload), operation_id),
        )

    def result_content(self, result_ref: str) -> str:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT result_payload FROM agent_operations "
                "WHERE result_ref=%s AND status='completed'",
                (result_ref,),
            ).fetchone()
        if row is None:
            raise ValueError("agent result reference not found")
        content = dict(row["result_payload"]).get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("agent result has no content")
        return content
