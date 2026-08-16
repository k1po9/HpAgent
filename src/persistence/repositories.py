"""Ownership-scoped repositories.  No method accepts a bare public resource ID."""
from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from .uow import UnitOfWork


class AccountRepository:
    def is_active(self, uow: UnitOfWork, account_id: UUID) -> bool:
        return uow.execute(
            "SELECT 1 FROM accounts WHERE account_id=%s AND status='active'", (account_id,)
        ).fetchone() is not None

    def require_active(self, uow: UnitOfWork, account_id: UUID) -> bool:
        return self.is_active(uow, account_id)


class ConversationRepository:
    def get_for_account(self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s" + suffix,
            (account_id, conversation_id),
        ).fetchone())

    def lock_active(self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID):
        return uow.execute(
            "SELECT * FROM conversations WHERE account_id=%s AND conversation_id=%s "
            "AND status='active' FOR UPDATE",
            (account_id, conversation_id),
        ).fetchone()

    def allocate_messages(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, count: int
    ) -> int:
        return int(uow.execute(
            "UPDATE conversations SET last_message_seq=last_message_seq+%s,updated_at=now() "
            "WHERE account_id=%s AND conversation_id=%s RETURNING last_message_seq",
            (count, account_id, conversation_id),
        ).fetchone()["last_message_seq"])

    def create(
        self, uow: UnitOfWork, conversation_id: UUID, account_id: UUID, title: str
    ) -> None:
        uow.execute(
            "INSERT INTO conversations(conversation_id,account_id,title) VALUES (%s,%s,%s)",
            (conversation_id, account_id, title),
        )


class MessageRepository:
    def context_messages(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, watermark: int
    ) -> list[dict[str, Any]]:
        """Load the frozen, model-visible Web history for exactly one Conversation."""
        return list(uow.execute(
            "SELECT message_id,role,status,content,sequence FROM messages "
            "WHERE account_id=%s AND conversation_id=%s AND sequence<=%s "
            "AND ((role='user' AND status='accepted') OR "
            "(role='assistant' AND status='completed')) ORDER BY sequence",
            (account_id, conversation_id, watermark),
        ).fetchall())

    def find_initial_by_client_request(
        self, uow: UnitOfWork, account_id: UUID, client_request_id: UUID
    ):
        return uow.execute(
            "SELECT m.message_id,m.conversation_id,m.content,m.sequence AS user_sequence,"
            "r.run_id,r.session_id,r.status AS run_status,r.trigger_message_id,"
            "a.message_id AS assistant_message_id,a.sequence AS assistant_sequence,"
            "a.status AS assistant_status "
            "FROM messages m JOIN runs r ON r.trigger_message_id=m.message_id "
            "AND r.retry_of_run_id IS NULL JOIN messages a ON a.produced_by_run_id=r.run_id "
            "WHERE m.account_id=%s "
            "AND m.client_request_id=%s",
            (account_id, client_request_id),
        ).fetchone()

    def insert_user(
        self, uow: UnitOfWork, message_id: UUID, account_id: UUID,
        conversation_id: UUID, content: str, sequence: int, client_request_id: UUID
    ) -> None:
        uow.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,content,"
            "sequence,client_request_id) VALUES (%s,%s,%s,'user','accepted',%s,%s,%s)",
            (message_id, account_id, conversation_id, content, sequence, client_request_id),
        )

    def insert_assistant(
        self, uow: UnitOfWork, message_id: UUID, account_id: UUID,
        conversation_id: UUID, sequence: int, run_id: UUID
    ) -> None:
        uow.execute(
            "INSERT INTO messages(message_id,account_id,conversation_id,role,status,sequence,"
            "produced_by_run_id) VALUES (%s,%s,%s,'assistant','pending',%s,%s)",
            (message_id, account_id, conversation_id, sequence, run_id),
        )

    def set_terminal(
        self, uow: UnitOfWork, run_id: UUID, status: str, content: str | None = None
    ) -> None:
        uow.execute(
            "UPDATE messages SET status=%s,content=COALESCE(%s,content),completed_at=now() "
            "WHERE produced_by_run_id=%s AND status='pending'",
            (status, content, run_id),
        )


class RunRepository:
    def get_for_account(self, uow: UnitOfWork, account_id: UUID, run_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT * FROM runs WHERE account_id=%s AND run_id=%s" + suffix,
            (account_id, run_id),
        ).fetchone())

    def context_messages(self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, watermark: int) -> list[dict[str, Any]]:
        """Compatibility alias; new Web context code uses MessageRepository."""
        return MessageRepository().context_messages(uow, account_id, conversation_id, watermark)

    def context_subject(
        self, uow: UnitOfWork, account_id: UUID, run_id: UUID
    ) -> dict[str, Any] | None:
        """Load the complete owned Run/Session/Conversation chain for Context assembly."""
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT r.run_id,r.account_id,r.conversation_id,r.session_id,"
            "r.trigger_message_id,r.context_message_seq,r.agent_strategy,r.status AS run_status,"
            "s.status AS session_status,s.workspace_ref,"
            "t.role AS trigger_role,t.status AS trigger_status,t.content AS trigger_content "
            "FROM runs r JOIN conversations c ON c.account_id=r.account_id "
            "AND c.conversation_id=r.conversation_id JOIN sessions s "
            "ON s.account_id=r.account_id AND s.conversation_id=r.conversation_id "
            "AND s.session_id=r.session_id JOIN messages t ON t.account_id=r.account_id "
            "AND t.conversation_id=r.conversation_id AND t.message_id=r.trigger_message_id "
            "WHERE r.account_id=%s AND r.run_id=%s",
            (account_id, run_id),
        ).fetchone())

    def discover_conversation(
        self, uow: UnitOfWork, account_id: UUID, run_id: UUID
    ) -> UUID | None:
        row = uow.execute(
            "SELECT conversation_id FROM runs WHERE account_id=%s AND run_id=%s",
            (account_id, run_id),
        ).fetchone()
        return cast(UUID | None, row["conversation_id"] if row else None)

    def has_active(self, uow: UnitOfWork, conversation_id: UUID) -> bool:
        return uow.execute(
            "SELECT 1 FROM runs WHERE conversation_id=%s "
            "AND status IN ('queued','running','cancelling')",
            (conversation_id,),
        ).fetchone() is not None

    def direct_retry(self, uow: UnitOfWork, source_run_id: UUID):
        return uow.execute(
            "SELECT r.run_id,r.status,r.retry_of_run_id,m.message_id AS assistant_message_id,"
            "m.sequence AS assistant_sequence,m.status AS assistant_status "
            "FROM runs r JOIN messages m ON m.produced_by_run_id=r.run_id "
            "WHERE r.retry_of_run_id=%s",
            (source_run_id,),
        ).fetchone()

    def insert(
        self, uow: UnitOfWork, run_id: UUID, account_id: UUID, conversation_id: UUID,
        session_id: UUID, trigger_message_id: UUID, workflow_id: str,
        context_message_seq: int, retry_of_run_id: UUID | None = None,
        agent_strategy: str = "react",
    ) -> None:
        uow.execute(
            "INSERT INTO runs(run_id,account_id,conversation_id,session_id,trigger_message_id,"
            "retry_of_run_id,workflow_id,context_message_seq,agent_strategy) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (run_id, account_id, conversation_id, session_id, trigger_message_id,
             retry_of_run_id, workflow_id, context_message_seq, agent_strategy),
        )

    def set_cancelling(self, uow: UnitOfWork, run_id: UUID) -> None:
        uow.execute(
            "UPDATE runs SET status='cancelling',version=version+1,updated_at=now() "
            "WHERE run_id=%s AND status IN ('queued','running')",
            (run_id,),
        )

    def set_terminal(
        self, uow: UnitOfWork, run_id: UUID, status: str,
        failure_code: str | None = None, failure_message: str | None = None
    ) -> None:
        uow.execute(
            "UPDATE runs SET status=%s,failure_code=%s,failure_message=%s,finished_at=now(),"
            "version=version+1,updated_at=now() WHERE run_id=%s",
            (status, failure_code, failure_message, run_id),
        )


class SessionRepository:
    def get_active(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID
    ) -> dict[str, Any] | None:
        """Return the sole active Web session for an already-owned conversation."""
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT * FROM sessions WHERE account_id=%s AND conversation_id=%s "
            "AND status='active'",
            (account_id, conversation_id),
        ).fetchone())

    def get_or_create_active(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID, session_id: UUID
    ) -> UUID:
        """Get or create an active session after the caller locked Conversation.

        The Conversation row is the transaction serialization point.  This keeps
        the database partial unique index as the final invariant while avoiding a
        check-then-insert race for normal send/retry commands.
        """
        row = self.get_active(uow, account_id, conversation_id)
        if row:
            return cast(UUID, row["session_id"])
        sequence = uow.execute(
            "SELECT COALESCE(max(sequence),0)+1 AS sequence FROM sessions "
            "WHERE account_id=%s AND conversation_id=%s",
            (account_id, conversation_id),
        ).fetchone()["sequence"]
        uow.execute(
            "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,workspace_ref) "
            "VALUES (%s,%s,%s,%s,'account_repo')",
            (session_id, account_id, conversation_id, sequence),
        )
        return session_id

    def transition_active(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID,
        session_id: UUID, status: str,
    ) -> bool:
        """Leave the active state without ever touching a different conversation."""
        row = uow.execute(
            "UPDATE sessions SET status=%s,archived_at=CASE WHEN %s='archived' "
            "THEN now() ELSE archived_at END,updated_at=now(),version=version+1 "
            "WHERE account_id=%s AND conversation_id=%s AND session_id=%s "
            "AND status='active' RETURNING session_id",
            (status, status, account_id, conversation_id, session_id),
        ).fetchone()
        return row is not None

    def create_successor(
        self, uow: UnitOfWork, account_id: UUID, conversation_id: UUID,
        predecessor_session_id: UUID, session_id: UUID,
    ) -> UUID:
        sequence = uow.execute(
            "SELECT COALESCE(max(sequence),0)+1 AS sequence FROM sessions "
            "WHERE account_id=%s AND conversation_id=%s",
            (account_id, conversation_id),
        ).fetchone()["sequence"]
        uow.execute(
            "INSERT INTO sessions(session_id,account_id,conversation_id,sequence,"
            "predecessor_session_id,workspace_ref) VALUES (%s,%s,%s,%s,%s,'account_repo')",
            (session_id, account_id, conversation_id, sequence, predecessor_session_id),
        )
        return session_id


class WorkflowExecutionRepository:
    def has_current(self, uow: UnitOfWork, run_id: UUID) -> bool:
        return uow.execute(
            "SELECT 1 FROM workflow_executions WHERE run_id=%s AND is_current", (run_id,)
        ).fetchone() is not None


class IdempotencyRepository:
    def claim(
        self, uow: UnitOfWork, command_id: UUID, account_id: UUID, operation: str,
        key: str, request_hash: bytes, expires_at: Any
    ) -> dict[str, Any] | None:
        inserted = uow.execute(
            "INSERT INTO idempotency_commands(idempotency_command_id,account_id,operation,"
            "idempotency_key,request_hash,expires_at) VALUES (%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (account_id,operation,idempotency_key) DO NOTHING "
            "RETURNING idempotency_command_id",
            (command_id, account_id, operation, key, request_hash, expires_at),
        ).fetchone()
        if inserted:
            return None
        return cast(dict[str, Any] | None, uow.execute(
            "SELECT * FROM idempotency_commands WHERE account_id=%s AND operation=%s "
            "AND idempotency_key=%s FOR UPDATE",
            (account_id, operation, key),
        ).fetchone())

    def complete(
        self, uow: UnitOfWork, account_id: UUID, operation: str, key: str,
        response_status: int, body: str
    ) -> None:
        uow.execute(
            "UPDATE idempotency_commands SET status='completed',response_status=%s,"
            "response_body=%s::jsonb,completed_at=now() WHERE account_id=%s "
            "AND operation=%s AND idempotency_key=%s",
            (response_status, body, account_id, operation, key),
        )


class OutboxRepository:
    def enqueue(
        self, uow: UnitOfWork, event_id: UUID, account_id: UUID, event_type: str,
        business_key: str, conversation_id: UUID, run_id: UUID, payload: str
    ) -> None:
        uow.execute(
            "INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,"
            "conversation_id,run_id,payload) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) "
            "ON CONFLICT (business_key) DO NOTHING",
            (event_id, account_id, event_type, business_key, conversation_id, run_id, payload),
        )

    def claim_batch(
        self, uow: UnitOfWork, worker_id: str,
        owned_event_types: Collection[str], limit: int = 10
    ):
        return uow.execute(
            "WITH candidates AS (SELECT outbox_event_id FROM outbox_events "
            "WHERE status='pending' AND available_at<=now() AND event_type=ANY(%s) "
            "ORDER BY available_at,created_at,outbox_event_id "
            "FOR UPDATE SKIP LOCKED LIMIT %s) UPDATE outbox_events o SET status='processing',"
            "attempt_count=attempt_count+1,locked_at=now(),locked_by=%s,updated_at=now() "
            "FROM candidates c WHERE o.outbox_event_id=c.outbox_event_id RETURNING o.*",
            (list(owned_event_types), limit, worker_id),
        ).fetchall()

    def recover_expired(
        self,
        uow: UnitOfWork,
        older_than_seconds: int,
        owned_event_types: Collection[str],
    ) -> int:
        event_types = list(frozenset(owned_event_types))
        cursor = uow.execute(
            "UPDATE outbox_events SET status='pending',locked_at=NULL,locked_by=NULL,"
            "updated_at=now() WHERE status='processing' "
            "AND locked_at < now()-(%s * interval '1 second') "
            "AND event_type=ANY(%s)",
            (older_than_seconds, event_types),
        )
        return int(cursor.rowcount)

    def mark_processed(self, uow: UnitOfWork, event_id: UUID, worker_id: str) -> bool:
        return uow.execute(
            "UPDATE outbox_events SET status='processed',processed_at=now(),locked_at=NULL,"
            "locked_by=NULL,updated_at=now() WHERE outbox_event_id=%s AND status='processing' "
            "AND locked_by=%s RETURNING outbox_event_id",
            (event_id, worker_id),
        ).fetchone() is not None

    def lock_owned_event(self, uow: UnitOfWork, event_id: UUID, worker_id: str):
        return uow.execute(
            "SELECT * FROM outbox_events WHERE outbox_event_id=%s "
            "AND status='processing' AND locked_by=%s FOR UPDATE",
            (event_id, worker_id),
        ).fetchone()

    def discover_context(self, uow: UnitOfWork, event_id: UUID):
        return uow.execute(
            "SELECT account_id,conversation_id,run_id FROM outbox_events "
            "WHERE outbox_event_id=%s",
            (event_id,),
        ).fetchone()

    def mark_retryable_failure(
        self, uow: UnitOfWork, event_id: UUID, worker_id: str,
        error_code: str, error_message: str, next_available_at: datetime
    ) -> bool:
        return uow.execute(
            "UPDATE outbox_events SET status='pending',locked_at=NULL,locked_by=NULL,"
            "available_at=%s,last_error_code=%s,last_error_message=%s,updated_at=now() "
            "WHERE outbox_event_id=%s AND status='processing' AND locked_by=%s "
            "RETURNING outbox_event_id",
            (next_available_at, error_code, error_message, event_id, worker_id),
        ).fetchone() is not None

    def mark_dead_letter(
        self, uow: UnitOfWork, event_id: UUID, worker_id: str,
        error_code: str, error_message: str
    ) -> bool:
        return uow.execute(
            "UPDATE outbox_events SET status='dead_letter',locked_at=NULL,locked_by=NULL,"
            "last_error_code=%s,last_error_message=%s,updated_at=now() "
            "WHERE outbox_event_id=%s AND status='processing' AND locked_by=%s "
            "RETURNING outbox_event_id",
            (error_code, error_message, event_id, worker_id),
        ).fetchone() is not None
