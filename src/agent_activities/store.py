"""PostgreSQL durable data plane for Agent workflows.

Transactions are intentionally short. No model, tool, Redis, Hindsight, or
workspace call is made while a database transaction is open.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg.types.json import Jsonb

from agent_workflows.contracts import AGENT_SCHEMA_VERSION
from agent_workflows.lifecycle_contracts import FinishWaitInput, SegmentInput, WaitInput
from persistence.uow import UnitOfWork, retryable_transaction

from .fencing import execution_fence


class LeaseConflict(RuntimeError):
    code = "execution_lease_conflict"


class StaleFencingToken(RuntimeError):
    code = "stale_fencing_token"


class RunNotExecutable(RuntimeError):
    code = "run_not_executable"


class SegmentClosed(RuntimeError):
    code = "execution_segment_closed"


class TranscriptVersionConflict(RuntimeError):
    code = "transcript_version_conflict"


@dataclass(frozen=True)
class ToolOperationState:
    status: str
    result_payload: dict[str, Any] | None


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
            now = uow.execute("SELECT now() AS now").fetchone()["now"]
            active = row["lease_expires_at"] is not None and row["lease_expires_at"] > now
            if active and row.get("owner_segment_id") is not None:
                raise LeaseConflict("account is owned by an execution segment")
            if (
                row["owner_run_id"] is not None
                and str(row["owner_run_id"]) != run_id
                and active
            ):
                raise LeaseConflict(f"account lease owned by run {row['owner_run_id']}")
            # Only redelivery within a live interval retains its token.
            same_owner = str(row["owner_run_id"]) == run_id and active
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
                "AND lease_expires_at>clock_timestamp() "
                "AND EXISTS (SELECT 1 FROM runs WHERE run_id=%s AND account_id=%s "
                "AND status IN ('queued','running')) RETURNING lease_expires_at",
                (self.lease_ttl_seconds, UUID(account_id), UUID(run_id), fencing_token, UUID(run_id), UUID(account_id)),
            ).fetchone()
            if row is None:
                raise StaleFencingToken("execution lease is absent, expired, or fenced")
        return ExecutionLease(account_id, run_id, fencing_token, row["lease_expires_at"].isoformat())

    @retryable_transaction
    def release_lease(self, account_id: str, run_id: str, fencing_token: int) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "UPDATE account_execution_leases SET owner_run_id=NULL,owner_segment_id=NULL,lease_expires_at=NULL,"
                "updated_at=now() WHERE account_id=%s AND owner_run_id=%s "
                "AND fencing_token=%s RETURNING account_id",
                (UUID(account_id), UUID(run_id), fencing_token),
            ).fetchone()
            return row is not None

    def run_identity(self, run_id: str) -> dict[str, Any]:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT run_id,account_id,conversation_id,session_id,trigger_message_id,"
                "agent_strategy,status,run_kind FROM runs WHERE run_id=%s",
                (UUID(run_id),),
            ).fetchone()
        if row is None:
            raise ValueError("run not found")
        return {key: str(value) if isinstance(value, UUID) else value for key, value in row.items()}

    def operation_result(self, operation_id: str) -> dict[str, Any] | None:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
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
            self._assert_fence(uow)
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
            self._assert_fence(uow)
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
    def begin_tool_operation(self, operation_id: str, run_id: str) -> ToolOperationState:
        """Start a tool attempt without erasing an unacknowledged intent."""
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            inserted = uow.execute(
                "INSERT INTO agent_operations(operation_id,run_id,operation_type) "
                "VALUES (%s,%s,'tool') ON CONFLICT (operation_id) DO NOTHING "
                "RETURNING operation_id",
                (operation_id, UUID(run_id)),
            ).fetchone()
            if inserted is not None:
                return ToolOperationState("started", None)
            row = uow.execute(
                "SELECT run_id,operation_type,status,result_payload FROM agent_operations "
                "WHERE operation_id=%s FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if str(row["run_id"]) != run_id or row["operation_type"] != "tool":
                raise ValueError("operation_id identity mismatch")
            status = str(row["status"])
            payload = dict(row["result_payload"]) if row["result_payload"] else None
            if status in {"completed", "intent_recorded", "uncertain"}:
                uow.execute(
                    "UPDATE agent_operations SET attempt_count=attempt_count+1,"
                    "updated_at=now() WHERE operation_id=%s",
                    (operation_id,),
                )
                return ToolOperationState(status, payload)
            uow.execute(
                "UPDATE agent_operations SET status='started',error_code=NULL,"
                "result_payload=NULL,attempt_count=attempt_count+1,updated_at=now() "
                "WHERE operation_id=%s",
                (operation_id,),
            )
            return ToolOperationState("started", None)

    @retryable_transaction
    def fail_operation(self, operation_id: str, error_code: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
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
            self._assert_fence(uow)
            row = uow.execute(
                "UPDATE agent_operations SET status='intent_recorded',result_payload=%s,updated_at=now() "
                "WHERE operation_id=%s AND status='started' RETURNING operation_id",
                (Jsonb(intent), operation_id),
            ).fetchone()
            if row is None:
                raise ValueError("tool intent operation is not active")

    @retryable_transaction
    def mark_operation_uncertain(self, operation_id: str, error_code: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            uow.execute(
                "UPDATE agent_operations SET status='uncertain',error_code=%s,updated_at=now() "
                "WHERE operation_id=%s AND status IN ('intent_recorded','uncertain')",
                (error_code, operation_id),
            )

    @retryable_transaction
    def create_transcript(
        self,
        *,
        transcript_id: str,
        run_id: str,
        account_id: str,
        conversation_id: str | None = None,
        session_id: str | None = None,
        messages: list[dict[str, Any]],
        operation_id: str,
    ) -> int:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            owner = uow.execute(
                "SELECT account_id,conversation_id,session_id FROM runs WHERE run_id=%s",
                (UUID(run_id),),
            ).fetchone()
            expected = (UUID(account_id), UUID(conversation_id) if conversation_id else None,
                        UUID(session_id) if session_id else None)
            if owner is None or tuple(owner[key] for key in (
                "account_id", "conversation_id", "session_id",
            )) != expected:
                raise ValueError("transcript context does not match Run ownership")
            uow.execute(
                "INSERT INTO agent_transcripts(transcript_id,run_id,account_id,conversation_id,session_id) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT (run_id) DO NOTHING",
                (
                    transcript_id,
                    UUID(run_id),
                    UUID(account_id),
                    UUID(conversation_id) if conversation_id else None,
                    UUID(session_id) if session_id else None,
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
                "schema_version": AGENT_SCHEMA_VERSION,
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
            self._assert_fence(uow)
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
            self._assert_fence(uow)
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
            self._assert_fence(uow)
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
            self._assert_fence(uow)
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


    @staticmethod
    def _active_run(uow, account_id, run_id):
        row = uow.execute(
            "SELECT status FROM runs WHERE account_id=%s AND run_id=%s FOR UPDATE",
            (UUID(account_id), UUID(run_id)),
        ).fetchone()
        if row is None or row["status"] not in {"queued", "running"}:
            raise RunNotExecutable("Run is cancelled, terminal or not owned by this account")

    @staticmethod
    def _assert_fence(uow):
        fence = execution_fence.get()
        if fence is None:
            return
        account_id, run_id, token = fence
        # Same lock order as acquire: Run then account lease. No network calls
        # while these short transaction locks are held.
        run = uow.execute(
            "SELECT status FROM runs WHERE account_id=%s AND run_id=%s FOR SHARE",
            (UUID(account_id), UUID(run_id)),
        ).fetchone()
        row = uow.execute(
            "SELECT fencing_token FROM account_execution_leases WHERE account_id=%s "
            "AND owner_run_id=%s AND fencing_token=%s "
            "AND lease_expires_at>clock_timestamp() FOR SHARE",
            (UUID(account_id), UUID(run_id), token),
        ).fetchone()
        if row is None or run is None or run["status"] not in {"queued", "running"}:
            raise StaleFencingToken("late execution cannot read or commit durable state")

    @retryable_transaction
    def acquire_segment(self, request: SegmentInput) -> int:
        with UnitOfWork(self.database_url) as uow:
            self._active_run(uow, request.account_id, request.run_id)
            waiting = uow.execute(
                "SELECT 1 FROM agent_run_waits WHERE run_id=%s AND state='waiting'",
                (UUID(request.run_id),),
            ).fetchone()
            if waiting:
                raise LeaseConflict("Run is suspended")
            uow.execute(
                "INSERT INTO agent_execution_segments(segment_id,run_id,account_id,state) "
                "VALUES (%s,%s,%s,'requested') ON CONFLICT DO NOTHING",
                (request.segment_id, UUID(request.run_id), UUID(request.account_id)),
            )
            segment = uow.execute(
                "SELECT * FROM agent_execution_segments WHERE segment_id=%s FOR UPDATE",
                (request.segment_id,),
            ).fetchone()
            if (str(segment["run_id"]) != request.run_id or
                    str(segment["account_id"]) != request.account_id or segment["state"] == "released"):
                raise SegmentClosed("execution segment closed or owner mismatch")
            uow.execute(
                "INSERT INTO account_execution_leases(account_id) VALUES (%s) ON CONFLICT DO NOTHING",
                (UUID(request.account_id),),
            )
            lease = uow.execute(
                "SELECT *,lease_expires_at>clock_timestamp() AS active "
                "FROM account_execution_leases WHERE account_id=%s FOR UPDATE",
                (UUID(request.account_id),),
            ).fetchone()
            same = lease["owner_segment_id"] == request.segment_id
            if lease["active"] and not same:
                raise LeaseConflict("account is executing another segment")
            token = int(lease["fencing_token"]) + (0 if same and lease["active"] else 1)
            uow.execute(
                "UPDATE account_execution_leases SET owner_run_id=%s,owner_segment_id=%s,"
                "fencing_token=%s,lease_expires_at=clock_timestamp()+(%s*interval '1 second'),"
                "updated_at=now() WHERE account_id=%s",
                (UUID(request.run_id), request.segment_id, token, self.lease_ttl_seconds, UUID(request.account_id)),
            )
            uow.execute(
                "UPDATE agent_execution_segments SET state='active',fencing_token=%s,updated_at=now() "
                "WHERE segment_id=%s", (token, request.segment_id),
            )
            return token

    @retryable_transaction
    def release_segment(self, request: SegmentInput) -> None:
        with UnitOfWork(self.database_url) as uow:
            # Terminal/cancelled Runs still need cleanup. Tombstone the segment
            # even if the acquire Activity result was lost or never delivered.
            uow.execute("SELECT run_id FROM runs WHERE run_id=%s FOR UPDATE", (UUID(request.run_id),))
            uow.execute(
                "INSERT INTO agent_execution_segments(segment_id,run_id,account_id,state) "
                "VALUES (%s,%s,%s,'released') ON CONFLICT DO NOTHING",
                (request.segment_id, UUID(request.run_id), UUID(request.account_id)),
            )
            uow.execute(
                "UPDATE agent_execution_segments SET state='released',updated_at=now() "
                "WHERE segment_id=%s AND run_id=%s AND account_id=%s",
                (request.segment_id, UUID(request.run_id), UUID(request.account_id)),
            )
            uow.execute(
                "UPDATE account_execution_leases SET owner_run_id=NULL,owner_segment_id=NULL,"
                "lease_expires_at=NULL,updated_at=now() "
                "WHERE account_id=%s AND owner_run_id=%s AND owner_segment_id=%s",
                (UUID(request.account_id), UUID(request.run_id), request.segment_id),
            )

    @retryable_transaction
    def begin_wait(self, request: WaitInput) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._active_run(uow, request.account_id, request.run_id)
            owned = uow.execute(
                "SELECT 1 FROM account_execution_leases WHERE account_id=%s AND owner_run_id=%s "
                "AND lease_expires_at>clock_timestamp()",
                (UUID(request.account_id), UUID(request.run_id)),
            ).fetchone()
            if owned:
                raise LeaseConflict("durable wait requires released execution resources")
            uow.execute(
                "INSERT INTO agent_run_waits(wait_id,run_id,account_id,operation_id,reason,resume_ref,"
                "deadline,state) VALUES (%s,%s,%s,%s,%s,%s,%s,'waiting') ON CONFLICT DO NOTHING",
                (request.wait_id, UUID(request.run_id), UUID(request.account_id), request.operation_id,
                 request.reason, request.resume_ref, request.deadline),
            )

    @retryable_transaction
    def finish_wait(self, request: FinishWaitInput) -> bool:
        wait = request.wait
        with UnitOfWork(self.database_url) as uow:
            run = uow.execute(
                "SELECT status FROM runs WHERE account_id=%s AND run_id=%s FOR UPDATE",
                (UUID(wait.account_id), UUID(wait.run_id)),
            ).fetchone()
            active = run is not None and run["status"] in {"queued", "running"}
            uow.execute(
                "UPDATE agent_run_waits SET state=%s,updated_at=now() "
                "WHERE wait_id=%s AND account_id=%s AND run_id=%s AND state='waiting'",
                (request.state if active else 'cancelled', wait.wait_id, UUID(wait.account_id), UUID(wait.run_id)),
            )
            return active
