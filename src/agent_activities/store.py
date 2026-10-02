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
from run_domain.results import ResultReceiptService

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
    execution_id: str


def preserve_late_result(function):
    from functools import wraps

    @wraps(function)
    def wrapped(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except StaleFencingToken:
            fence = execution_fence.get()
            if fence is not None:
                operation_id = kwargs.get("operation_id", args[0] if args else None)
                payload = kwargs.get("result_payload", args[2] if len(args) > 2 else None)
                if operation_id and payload is not None:
                    account, run, execution, token = fence
                    ResultReceiptService(self.database_url).receive(
                        account, run, execution, operation_id, token, payload
                    )
            raise

    return wrapped


class AgentDataStore:
    def __init__(self, database_url: object, *, lease_ttl_seconds: int = 3600):
        if lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self.database_url = database_url
        self.lease_ttl_seconds = lease_ttl_seconds

    @staticmethod
    def _execution(uow, account_id, run_id, execution_id=None):
        row = uow.execute(
            "SELECT * FROM run_executions WHERE account_id=%s AND run_id=%s "
            "AND (%s::uuid IS NULL OR execution_id=%s) AND role='root' FOR UPDATE",
            (
                UUID(account_id),
                UUID(run_id),
                UUID(execution_id) if execution_id else None,
                UUID(execution_id) if execution_id else None,
            ),
        ).fetchone()
        if row is None:
            raise RunNotExecutable("Execution does not belong to the Run and Account")
        return row

    @retryable_transaction
    def acquire_lease(self, account_id: str, run_id: str, execution_id=None) -> ExecutionLease:
        with UnitOfWork(self.database_url) as uow:
            self._active_run(uow, account_id, run_id)
            execution = self._execution(uow, account_id, run_id, execution_id)
            return self._acquire(uow, account_id, run_id, str(execution["execution_id"]), None)

    def _acquire(self, uow, account_id, run_id, execution_id, segment_id):
        uow.execute(
            "INSERT INTO execution_attempt_leases(account_id,run_id,execution_id) VALUES (%s,%s,%s) "
            "ON CONFLICT (execution_id) DO NOTHING",
            (UUID(account_id), UUID(run_id), UUID(execution_id)),
        )
        row = uow.execute(
            "SELECT *,lease_expires_at>clock_timestamp() AS active FROM execution_attempt_leases "
            "WHERE execution_id=%s FOR UPDATE",
            (UUID(execution_id),),
        ).fetchone()
        same = row["active"] and row["owner_segment_id"] == segment_id
        if row["active"] and not same:
            raise LeaseConflict("Execution is owned by another segment")
        token = int(row["fencing_token"]) + (0 if same else 1)
        expires = uow.execute(
            "UPDATE execution_attempt_leases SET owner_segment_id=%s,fencing_token=%s,"
            "lease_expires_at=clock_timestamp()+(%s*interval '1 second'),updated_at=now() "
            "WHERE execution_id=%s RETURNING lease_expires_at",
            (segment_id, token, self.lease_ttl_seconds, UUID(execution_id)),
        ).fetchone()["lease_expires_at"]
        uow.execute(
            "UPDATE run_executions SET attempt_no=%s,updated_at=now() WHERE execution_id=%s",
            (token, UUID(execution_id)),
        )
        return ExecutionLease(account_id, run_id, token, expires.isoformat(), execution_id)

    @retryable_transaction
    def validate_and_renew_lease(
        self, account_id: str, run_id: str, fencing_token: int, execution_id=None
    ) -> ExecutionLease:
        with UnitOfWork(self.database_url) as uow:
            try:
                self._active_run(uow, account_id, run_id)
            except RunNotExecutable as exc:
                raise StaleFencingToken("execution progression has been revoked") from exc
            execution = self._execution(uow, account_id, run_id, execution_id)
            execution_id = str(execution["execution_id"])
            row = uow.execute(
                "UPDATE execution_attempt_leases SET "
                "lease_expires_at=clock_timestamp()+(%s * interval '1 second'),updated_at=now() "
                "WHERE account_id=%s AND run_id=%s AND execution_id=%s AND fencing_token=%s "
                "AND lease_expires_at>clock_timestamp() RETURNING lease_expires_at",
                (
                    self.lease_ttl_seconds,
                    UUID(account_id),
                    UUID(run_id),
                    UUID(execution_id),
                    fencing_token,
                ),
            ).fetchone()
            if row is None:
                raise StaleFencingToken("execution lease is absent, expired, or fenced")
        return ExecutionLease(
            account_id, run_id, fencing_token, row["lease_expires_at"].isoformat(), execution_id
        )

    @retryable_transaction
    def release_lease(self, account_id: str, run_id: str, fencing_token: int) -> bool:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "UPDATE execution_attempt_leases SET owner_segment_id=NULL,lease_expires_at=NULL,"
                "updated_at=now() WHERE account_id=%s AND run_id=%s "
                "AND fencing_token=%s RETURNING execution_id",
                (UUID(account_id), UUID(run_id), fencing_token),
            ).fetchone()
            return row is not None

    def run_identity(self, run_id: str) -> dict[str, Any]:
        with UnitOfWork(self.database_url) as uow:
            row = uow.execute(
                "SELECT r.*,e.execution_id,(SELECT origin FROM messages "
                "WHERE message_id=r.trigger_message_id) AS origin FROM runs r "
                "JOIN run_executions e ON e.account_id=r.account_id AND e.run_id=r.run_id "
                "AND e.role='root' WHERE r.run_id=%s",
                (UUID(run_id),),
            ).fetchone()
        if row is None:
            raise ValueError("run not found")
        return {key: str(value) if isinstance(value, UUID) else value for key, value in row.items()}

    @staticmethod
    def _owned_record(uow, table, column, value):
        fence = execution_fence.get()
        if fence is None:
            return
        account, run, execution, _ = fence
        row = uow.execute(
            f"SELECT 1 FROM {table} WHERE {column}=%s AND account_id=%s AND run_id=%s "
            "AND execution_id=%s",
            (value, UUID(account), UUID(run), UUID(execution)),
        ).fetchone()
        if row is None:
            raise StaleFencingToken("record is outside the Execution context")

    def operation_result(self, operation_id: str) -> dict[str, Any] | None:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            self._owned_record(uow, "execution_operations", "operation_id", operation_id)
            row = uow.execute(
                "SELECT result_payload FROM execution_operations "
                "WHERE operation_id=%s AND status='completed'",
                (operation_id,),
            ).fetchone()
        return dict(row["result_payload"]) if row else None

    def tool_call_arguments(self, arguments_ref: str, tool_call_id: str) -> dict[str, Any]:
        decision_ref, separator, referenced_call_id = arguments_ref.rpartition("#")
        if not separator or referenced_call_id != tool_call_id:
            raise ValueError("invalid tool arguments reference")
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            self._owned_record(uow, "execution_operations", "result_ref", decision_ref)
            row = uow.execute(
                "SELECT result_payload FROM execution_operations "
                "WHERE result_ref=%s AND status='completed'",
                (decision_ref,),
            ).fetchone()
        if row is None:
            raise ValueError("model decision reference not found")
        arguments_by_call = dict(row["result_payload"]).get("tool_call_arguments", {})
        arguments = arguments_by_call.get(tool_call_id)
        if not isinstance(arguments, dict):
            raise ValueError("tool call arguments not found")
        return dict(arguments)

    @retryable_transaction
    def begin_operation(
        self, operation_id: str, run_id: str, operation_type: str
    ) -> dict[str, Any] | None:
        """Return the prior compact result when completed, else mark an attempt."""
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            fence = execution_fence.get()
            if fence is not None and fence[1] != run_id:
                raise StaleFencingToken("operation is outside the Execution context")
            inserted = uow.execute(
                "INSERT INTO execution_operations(operation_id,run_id,operation_type) "
                "VALUES (%s,%s,%s) ON CONFLICT (operation_id) DO NOTHING "
                "RETURNING operation_id",
                (operation_id, UUID(run_id), operation_type),
            ).fetchone()
            if inserted is not None:
                ResultReceiptService.register_attempt(uow, operation_id, execution_fence.get())
                return None
            row = uow.execute(
                "SELECT run_id,operation_type,status,result_payload FROM execution_operations "
                "WHERE operation_id=%s FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if str(row["run_id"]) != run_id or row["operation_type"] != operation_type:
                raise ValueError("operation_id identity mismatch")
            if row["status"] == "completed":
                return dict(row["result_payload"])
            uow.execute(
                "UPDATE execution_operations SET status='started',error_code=NULL,"
                "attempt_count=attempt_count+1,updated_at=now() WHERE operation_id=%s",
                (operation_id,),
            )
            ResultReceiptService.register_attempt(uow, operation_id, execution_fence.get())
        return None

    @retryable_transaction
    def begin_tool_operation(self, operation_id: str, run_id: str) -> ToolOperationState:
        """Start a tool attempt without erasing an unacknowledged intent."""
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            fence = execution_fence.get()
            if fence is not None and fence[1] != run_id:
                raise StaleFencingToken("operation is outside the Execution context")
            inserted = uow.execute(
                "INSERT INTO execution_operations(operation_id,run_id,operation_type) "
                "VALUES (%s,%s,'tool') ON CONFLICT (operation_id) DO NOTHING "
                "RETURNING operation_id",
                (operation_id, UUID(run_id)),
            ).fetchone()
            if inserted is not None:
                ResultReceiptService.register_attempt(uow, operation_id, execution_fence.get())
                return ToolOperationState("started", None)
            row = uow.execute(
                "SELECT run_id,operation_type,status,result_payload FROM execution_operations "
                "WHERE operation_id=%s FOR UPDATE",
                (operation_id,),
            ).fetchone()
            if str(row["run_id"]) != run_id or row["operation_type"] != "tool":
                raise ValueError("operation_id identity mismatch")
            status = str(row["status"])
            payload = dict(row["result_payload"]) if row["result_payload"] else None
            if status == "completed":
                return ToolOperationState(status, payload)
            if status in {"intent_recorded", "uncertain"}:
                uow.execute(
                    "UPDATE execution_operations SET attempt_count=attempt_count+1,"
                    "updated_at=now() WHERE operation_id=%s",
                    (operation_id,),
                )
                ResultReceiptService.register_attempt(uow, operation_id, execution_fence.get())
                return ToolOperationState(status, payload)
            uow.execute(
                "UPDATE execution_operations SET status='started',error_code=NULL,"
                "result_payload=NULL,attempt_count=attempt_count+1,updated_at=now() "
                "WHERE operation_id=%s",
                (operation_id,),
            )
            ResultReceiptService.register_attempt(uow, operation_id, execution_fence.get())
            return ToolOperationState("started", None)

    @retryable_transaction
    def fail_operation(self, operation_id: str, error_code: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            self._owned_record(uow, "execution_operations", "operation_id", operation_id)
            uow.execute(
                "UPDATE execution_operations SET status='failed',error_code=%s,updated_at=now() "
                "WHERE operation_id=%s AND status<>'completed'",
                (error_code, operation_id),
            )

    @retryable_transaction
    def record_operation_intent(self, operation_id: str, intent: dict[str, Any]) -> None:
        """Persist compact side-effect intent before invoking an external tool."""
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            self._owned_record(uow, "execution_operations", "operation_id", operation_id)
            row = uow.execute(
                "UPDATE execution_operations SET status='intent_recorded',result_payload=%s,updated_at=now() "
                "WHERE operation_id=%s AND status='started' RETURNING operation_id",
                (Jsonb(intent), operation_id),
            ).fetchone()
            if row is None:
                raise ValueError("tool intent operation is not active")

    @retryable_transaction
    def mark_operation_uncertain(self, operation_id: str, error_code: str) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            self._owned_record(uow, "execution_operations", "operation_id", operation_id)
            uow.execute(
                "UPDATE execution_operations SET status='uncertain',error_code=%s,updated_at=now() "
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
        execution_id: str,
        messages: list[dict[str, Any]],
        operation_id: str,
    ) -> int:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            owner = uow.execute(
                "SELECT account_id,conversation_id,session_id FROM runs WHERE run_id=%s",
                (UUID(run_id),),
            ).fetchone()
            self._execution(uow, account_id, run_id, execution_id)
            expected = (
                UUID(account_id),
                UUID(conversation_id) if conversation_id else None,
                UUID(session_id) if session_id else None,
            )
            if (
                owner is None
                or tuple(
                    owner[key]
                    for key in (
                        "account_id",
                        "conversation_id",
                        "session_id",
                    )
                )
                != expected
            ):
                raise ValueError("transcript context does not match Run ownership")
            uow.execute(
                "INSERT INTO agent_transcripts(transcript_id,run_id,account_id,conversation_id,session_id,execution_id) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (execution_id) DO NOTHING",
                (
                    transcript_id,
                    UUID(run_id),
                    UUID(account_id),
                    UUID(conversation_id) if conversation_id else None,
                    UUID(session_id) if session_id else None,
                    UUID(execution_id),
                ),
            )
            transcript = uow.execute(
                "SELECT transcript_id,version FROM agent_transcripts WHERE execution_id=%s FOR UPDATE",
                (UUID(execution_id),),
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
            self._complete_operation(uow, operation_id, str(payload["context_ref"]), payload)
        return 1

    def load_messages(self, transcript_id: str) -> tuple[list[dict[str, Any]], int]:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            self._owned_record(uow, "agent_transcripts", "transcript_id", transcript_id)
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

    @preserve_late_result
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
            self._owned_record(uow, "agent_transcripts", "transcript_id", transcript_id)
            self._owned_record(uow, "execution_operations", "operation_id", operation_id)
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
                "UPDATE agent_transcripts SET version=%s,updated_at=now() WHERE transcript_id=%s",
                (version, transcript_id),
            )
            result_payload["transcript_version"] = version
            self._complete_operation(uow, operation_id, result_ref, result_payload)
        return version

    @preserve_late_result
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
        AgentDataStore._owned_record(uow, "execution_operations", "operation_id", operation_id)
        prior = uow.execute(
            "SELECT status,result_ref,result_payload FROM execution_operations "
            "WHERE operation_id=%s FOR UPDATE",
            (operation_id,),
        ).fetchone()
        if prior is None:
            raise ValueError("operation not found")
        if prior["status"] == "completed":
            if prior["result_ref"] != result_ref or prior["result_payload"] != result_payload:
                raise ValueError("completed operation result conflict")
            return
        fence = execution_fence.get()
        if fence is not None:
            from run_domain.results import result_digest

            attempt = uow.execute(
                "SELECT * FROM execution_operation_attempts WHERE operation_id=%s "
                "AND fencing_token=%s",
                (operation_id, fence[3]),
            ).fetchone()
            if attempt is None:
                raise ValueError("result attempt was not registered")
            uow.execute(
                "INSERT INTO execution_result_receipts(account_id,run_id,execution_id,operation_id,"
                "attempt_no,requirement_revision,result_ref,digest,disposition) "
                "SELECT account_id,run_id,execution_id,operation_id,%s,requirement_revision,%s,%s,'current' "
                "FROM execution_operations WHERE operation_id=%s ON CONFLICT DO NOTHING",
                (
                    attempt["attempt_no"],
                    attempt["receipt_ref"],
                    result_digest(result_payload),
                    operation_id,
                ),
            )
        uow.execute(
            "UPDATE execution_operations SET status='completed' ,result_ref=%s,result_payload=%s,"
            "error_code=NULL,completed_at=now(),updated_at=now() WHERE operation_id=%s",
            (result_ref, Jsonb(result_payload), operation_id),
        )

    def result_content(self, result_ref: str, run_id: str) -> str:
        with UnitOfWork(self.database_url) as uow:
            self._assert_fence(uow)
            row = uow.execute(
                "SELECT result_payload FROM execution_operations "
                "WHERE result_ref=%s AND run_id=%s AND status='completed'",
                (result_ref, UUID(run_id)),
            ).fetchone()
        if row is None:
            raise ValueError("agent result reference not found")
        content = dict(row["result_payload"]).get("content")
        if not isinstance(content, str) or not content:
            raise ValueError("agent result has no content")
        return content

    @staticmethod
    def _active_run(uow, account_id, run_id):
        from persistence.repositories import AccountRepository
        from run_domain.lifecycle import RunLifecycleService
        from web_domain.errors import DomainError

        try:
            run = RunLifecycleService.lock(uow, UUID(account_id), UUID(run_id))
            if not AccountRepository().require_active(uow, UUID(account_id)):
                raise RunNotExecutable("Account is disabled")
            if run["source_kind"] == "work":
                RunLifecycleService.check_work(uow, run)
            if run["status"] not in {"queued", "running"}:
                raise RunNotExecutable("Run cannot execute")
            return run
        except DomainError as exc:
            raise RunNotExecutable("Run no longer owns progression") from exc

    @staticmethod
    def _assert_fence(uow):
        fence = execution_fence.get()
        if fence is None:
            return
        account_id, run_id, execution_id, token = fence
        try:
            AgentDataStore._active_run(uow, account_id, run_id)
            AgentDataStore._execution(uow, account_id, run_id, execution_id)
        except RunNotExecutable as exc:
            raise StaleFencingToken("late execution cannot read or commit durable state") from exc
        row = uow.execute(
            "SELECT fencing_token FROM execution_attempt_leases WHERE account_id=%s "
            "AND run_id=%s AND execution_id=%s AND fencing_token=%s "
            "AND lease_expires_at>clock_timestamp() FOR SHARE",
            (UUID(account_id), UUID(run_id), UUID(execution_id), token),
        ).fetchone()
        if row is None:
            raise StaleFencingToken("late execution cannot read or commit durable state")

    @retryable_transaction
    def acquire_segment(self, request: SegmentInput) -> int:
        with UnitOfWork(self.database_url) as uow:
            self._active_run(uow, request.account_id, request.run_id)
            self._execution(uow, request.account_id, request.run_id, request.execution_id)
            waiting = uow.execute(
                "SELECT 1 FROM agent_run_waits WHERE execution_id=%s AND state='waiting'",
                (UUID(request.execution_id),),
            ).fetchone()
            if waiting:
                raise LeaseConflict("Execution is suspended")
            uow.execute(
                "INSERT INTO agent_execution_segments(segment_id,run_id,account_id,execution_id,state) "
                "VALUES (%s,%s,%s,%s,'requested') ON CONFLICT DO NOTHING",
                (
                    request.segment_id,
                    UUID(request.run_id),
                    UUID(request.account_id),
                    UUID(request.execution_id),
                ),
            )
            segment = uow.execute(
                "SELECT * FROM agent_execution_segments WHERE segment_id=%s FOR UPDATE",
                (request.segment_id,),
            ).fetchone()
            if (
                str(segment["run_id"]) != request.run_id
                or str(segment["account_id"]) != request.account_id
                or str(segment["execution_id"]) != request.execution_id
                or segment["state"] == "released"
            ):
                raise SegmentClosed("execution segment closed or owner mismatch")
            lease = self._acquire(
                uow, request.account_id, request.run_id, request.execution_id, request.segment_id
            )
            uow.execute(
                "UPDATE agent_execution_segments SET state='active',fencing_token=%s,updated_at=now() "
                "WHERE segment_id=%s",
                (lease.fencing_token, request.segment_id),
            )
            return lease.fencing_token

    @retryable_transaction
    def release_segment(self, request: SegmentInput) -> None:
        from run_domain.lifecycle import RunLifecycleService

        with UnitOfWork(self.database_url) as uow:
            RunLifecycleService.lock(uow, UUID(request.account_id), UUID(request.run_id))
            self._execution(uow, request.account_id, request.run_id, request.execution_id)
            uow.execute(
                "INSERT INTO agent_execution_segments(segment_id,run_id,account_id,execution_id,state) "
                "VALUES (%s,%s,%s,%s,'released') ON CONFLICT DO NOTHING",
                (
                    request.segment_id,
                    UUID(request.run_id),
                    UUID(request.account_id),
                    UUID(request.execution_id),
                ),
            )
            uow.execute(
                "UPDATE agent_execution_segments SET state='released',updated_at=now() "
                "WHERE segment_id=%s AND run_id=%s AND account_id=%s AND execution_id=%s",
                (
                    request.segment_id,
                    UUID(request.run_id),
                    UUID(request.account_id),
                    UUID(request.execution_id),
                ),
            )
            uow.execute(
                "UPDATE execution_attempt_leases SET owner_segment_id=NULL,lease_expires_at=NULL,updated_at=now() "
                "WHERE account_id=%s AND run_id=%s AND execution_id=%s AND owner_segment_id=%s",
                (
                    UUID(request.account_id),
                    UUID(request.run_id),
                    UUID(request.execution_id),
                    request.segment_id,
                ),
            )

    @retryable_transaction
    def begin_wait(self, request: WaitInput) -> None:
        with UnitOfWork(self.database_url) as uow:
            self._active_run(uow, request.account_id, request.run_id)
            self._execution(uow, request.account_id, request.run_id, request.execution_id)
            owned = uow.execute(
                "SELECT 1 FROM execution_attempt_leases WHERE execution_id=%s AND lease_expires_at>clock_timestamp()",
                (UUID(request.execution_id),),
            ).fetchone()
            if owned:
                raise LeaseConflict("durable wait requires released execution resources")
            uow.execute(
                "INSERT INTO agent_run_waits(wait_id,run_id,account_id,execution_id,operation_id,reason,resume_ref,"
                "deadline,state) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'waiting') ON CONFLICT DO NOTHING",
                (
                    request.wait_id,
                    UUID(request.run_id),
                    UUID(request.account_id),
                    UUID(request.execution_id),
                    request.operation_id,
                    request.reason,
                    request.resume_ref,
                    request.deadline,
                ),
            )

    @retryable_transaction
    def finish_wait(self, request: FinishWaitInput) -> bool:
        wait = request.wait
        with UnitOfWork(self.database_url) as uow:
            try:
                self._active_run(uow, wait.account_id, wait.run_id)
                self._execution(uow, wait.account_id, wait.run_id, wait.execution_id)
                active = True
            except RunNotExecutable:
                active = False
            uow.execute(
                "UPDATE agent_run_waits SET state=%s,updated_at=now() "
                "WHERE wait_id=%s AND account_id=%s AND run_id=%s AND execution_id=%s AND state='waiting'",
                (
                    request.state if active else "cancelled",
                    wait.wait_id,
                    UUID(wait.account_id),
                    UUID(wait.run_id),
                    UUID(wait.execution_id),
                ),
            )
            return active
