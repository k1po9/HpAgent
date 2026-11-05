"""Ownership-scoped repositories.  No method accepts a bare public resource ID."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import Any, cast
from uuid import UUID

from .accounts import AccountRepository as AccountRepository
from .conversations import ConversationRepository as ConversationRepository
from .messages import MessageRepository as MessageRepository
from .runs import RunRepository as RunRepository
from .uow import UnitOfWork


class FileRepository:
    def list_ready_for_run(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        run_id: UUID,
    ) -> list[dict[str, Any]]:
        """Return the model-safe manifest for one account-owned Run."""
        return list(
            uow.execute(
                "SELECT rf.logical_name,rf.direction,sf.size_bytes,sf.encoding,sf.content_type "
                "FROM run_files rf JOIN stored_files sf ON sf.account_id=rf.account_id "
                "AND sf.file_id=rf.file_id "
                "WHERE rf.account_id=%s AND rf.run_id=%s AND sf.status='ready' "
                "ORDER BY rf.direction,rf.logical_name",
                (account_id, run_id),
            ).fetchall()
        )

    def insert_upload(
        self,
        uow: UnitOfWork,
        file_id: UUID,
        account_id: UUID,
        conversation_id: UUID | None,
        original_name: str,
        display_name: str,
        declared_size: int,
        content_type: str,
        declared_sha256: str | None,
        expires_at: datetime,
        *,
        source_workspace_id: UUID | None = None,
    ) -> None:
        uow.execute(
            "INSERT INTO stored_files(file_id,account_id,conversation_id,source_workspace_id,purpose,status,"
            "original_name,display_name,content_type,size_bytes,declared_sha256,expires_at) "
            "VALUES (%s,%s,%s,%s,'input','uploading',%s,%s,%s,%s,%s,%s)",
            (
                file_id,
                account_id,
                conversation_id,
                source_workspace_id,
                original_name,
                display_name,
                content_type,
                declared_size,
                declared_sha256,
                expires_at,
            ),
        )

    def get_for_account(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
        *,
        lock: bool = False,
    ) -> dict[str, Any] | None:
        suffix = " FOR UPDATE" if lock else ""
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM stored_files WHERE account_id=%s AND file_id=%s" + suffix,
                (account_id, file_id),
            ).fetchone(),
        )

    def mark_ready(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
        storage_key: str,
        size_bytes: int,
        sha256: str,
        encoding: str,
        expires_at: datetime,
    ) -> bool:
        return (
            uow.execute(
                "UPDATE stored_files SET status='ready',storage_key=%s,size_bytes=%s,sha256=%s,"
                "encoding=%s,ready_at=now(),expires_at=%s "
                "WHERE account_id=%s AND file_id=%s AND status='uploading' RETURNING file_id",
                (storage_key, size_bytes, sha256, encoding, expires_at, account_id, file_id),
            ).fetchone()
            is not None
        )

    def mark_rejected(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
        failure_code: str,
    ) -> None:
        uow.execute(
            "UPDATE stored_files SET status='rejected',failure_code=%s,storage_key=NULL,"
            "ready_at=NULL,sha256=NULL WHERE account_id=%s AND file_id=%s "
            "AND status='uploading'",
            (failure_code, account_id, file_id),
        )

    def mark_deleted_if_unbound(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        file_id: UUID,
    ) -> str:
        from web_domain.file_lifecycle import claim_file_deletion

        return cast(str, claim_file_deletion(uow, account_id, file_id))

    def lock_ready_inputs(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID,
        file_ids: Collection[UUID],
    ) -> list[dict[str, Any]]:
        if not file_ids:
            return []
        return list(
            uow.execute(
                "SELECT * FROM stored_files WHERE account_id=%s "
                "AND file_id=ANY(%s) AND status='ready' "
                "ORDER BY created_at,file_id FOR UPDATE",
                (account_id, list(file_ids)),
            ).fetchall()
        )

    def bind_inputs(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        conversation_id: UUID,
        message_id: UUID,
        run_id: UUID,
        files: list[dict[str, Any]],
    ) -> None:
        used_names: set[str] = set()
        if files:
            uow.execute(
                "UPDATE stored_files SET expires_at=NULL WHERE account_id=%s "
                "AND purpose='input' AND file_id=ANY(%s)",
                (account_id, [file["file_id"] for file in files]),
            )
        for ordinal, file in enumerate(files):
            logical_name = self._unique_logical_name(str(file["display_name"]), used_names)
            uow.execute(
                "INSERT INTO message_files(account_id,conversation_id,message_id,file_id,role,ordinal) "
                "VALUES (%s,%s,%s,%s,'input',%s)",
                (account_id, conversation_id, message_id, file["file_id"], ordinal),
            )
            uow.execute(
                "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,logical_name) "
                "VALUES (%s,%s,%s,%s,'input',%s)",
                (account_id, conversation_id, run_id, file["file_id"], logical_name),
            )

    def copy_retry_inputs(
        self,
        uow: UnitOfWork,
        source_run_id: UUID,
        target_run_id: UUID,
    ) -> None:
        uow.execute(
            "INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,logical_name) "
            "SELECT account_id,conversation_id,%s,file_id,'input',logical_name FROM run_files "
            "WHERE run_id=%s AND direction='input'",
            (target_run_id, source_run_id),
        )

    @staticmethod
    def _unique_logical_name(name: str, used: set[str]) -> str:
        safe = name.replace("/", "_").replace("\\", "_").strip() or "file"
        safe = safe[:255]
        candidate = safe
        stem, dot, suffix = safe.rpartition(".")
        base = stem if dot else safe
        extension = f".{suffix}" if dot else ""
        counter = 2
        while candidate.casefold() in used:
            marker = f"-{counter}"
            candidate = f"{base[: 255 - len(extension) - len(marker)]}{marker}{extension}"
            counter += 1
        used.add(candidate.casefold())
        return candidate


class RunBudgetRepository:
    def create_snapshot(
        self,
        uow: UnitOfWork,
        run_id: UUID,
        account_id: UUID,
        conversation_id: UUID | None,
        policy_version: str,
        mode: str,
        limits: str,
        final_response_reserve_tokens: int,
    ) -> None:
        uow.execute(
            "INSERT INTO run_budgets(run_id,account_id,conversation_id,policy_version,mode,"
            "limits,final_response_reserve_tokens) VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)",
            (
                run_id,
                account_id,
                conversation_id,
                policy_version,
                mode,
                limits,
                final_response_reserve_tokens,
            ),
        )


class WorkflowExecutionRepository:
    def has_current(self, uow: UnitOfWork, run_id: UUID) -> bool:
        return (
            uow.execute(
                "SELECT 1 FROM workflow_executions WHERE run_id=%s AND is_current", (run_id,)
            ).fetchone()
            is not None
        )


class IdempotencyRepository:
    def claim(
        self,
        uow: UnitOfWork,
        command_id: UUID,
        account_id: UUID,
        operation: str,
        key: str,
        request_hash: bytes,
        expires_at: Any,
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
        return cast(
            dict[str, Any] | None,
            uow.execute(
                "SELECT * FROM idempotency_commands WHERE account_id=%s AND operation=%s "
                "AND idempotency_key=%s FOR UPDATE",
                (account_id, operation, key),
            ).fetchone(),
        )

    def complete(
        self,
        uow: UnitOfWork,
        account_id: UUID,
        operation: str,
        key: str,
        response_status: int,
        body: str,
    ) -> None:
        uow.execute(
            "UPDATE idempotency_commands SET status='completed',response_status=%s,"
            "response_body=%s::jsonb,completed_at=now() WHERE account_id=%s "
            "AND operation=%s AND idempotency_key=%s",
            (response_status, body, account_id, operation, key),
        )


class OutboxRepository:
    def enqueue(
        self,
        uow: UnitOfWork,
        event_id: UUID,
        account_id: UUID,
        event_type: str,
        business_key: str,
        conversation_id: UUID | None,
        run_id: UUID,
        payload: str,
    ) -> None:
        uow.execute(
            "INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,"
            "conversation_id,run_id,payload) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) "
            "ON CONFLICT (business_key) DO NOTHING",
            (event_id, account_id, event_type, business_key, conversation_id, run_id, payload),
        )

    def claim_batch(
        self, uow: UnitOfWork, worker_id: str, owned_event_types: Collection[str], limit: int = 10
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
        return (
            uow.execute(
                "UPDATE outbox_events SET status='processed',processed_at=now(),locked_at=NULL,"
                "locked_by=NULL,updated_at=now() WHERE outbox_event_id=%s AND status='processing' "
                "AND locked_by=%s RETURNING outbox_event_id",
                (event_id, worker_id),
            ).fetchone()
            is not None
        )

    def lock_owned_event(self, uow: UnitOfWork, event_id: UUID, worker_id: str):
        return uow.execute(
            "SELECT * FROM outbox_events WHERE outbox_event_id=%s "
            "AND status='processing' AND locked_by=%s FOR UPDATE",
            (event_id, worker_id),
        ).fetchone()

    def discover_context(self, uow: UnitOfWork, event_id: UUID):
        return uow.execute(
            "SELECT account_id,conversation_id,run_id FROM outbox_events WHERE outbox_event_id=%s",
            (event_id,),
        ).fetchone()

    def mark_retryable_failure(
        self,
        uow: UnitOfWork,
        event_id: UUID,
        worker_id: str,
        error_code: str,
        error_message: str,
        next_available_at: datetime,
    ) -> bool:
        return (
            uow.execute(
                "UPDATE outbox_events SET status='pending',locked_at=NULL,locked_by=NULL,"
                "available_at=%s,last_error_code=%s,last_error_message=%s,updated_at=now() "
                "WHERE outbox_event_id=%s AND status='processing' AND locked_by=%s "
                "RETURNING outbox_event_id",
                (next_available_at, error_code, error_message, event_id, worker_id),
            ).fetchone()
            is not None
        )

    def mark_dead_letter(
        self, uow: UnitOfWork, event_id: UUID, worker_id: str, error_code: str, error_message: str
    ) -> bool:
        return (
            uow.execute(
                "UPDATE outbox_events SET status='dead_letter',locked_at=NULL,locked_by=NULL,"
                "last_error_code=%s,last_error_message=%s,updated_at=now() "
                "WHERE outbox_event_id=%s AND status='processing' AND locked_by=%s "
                "RETURNING outbox_event_id",
                (error_code, error_message, event_id, worker_id),
            ).fetchone()
            is not None
        )
