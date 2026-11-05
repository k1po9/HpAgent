"""Outbox queue SQL in a borrowed UoW, preserving consumer ownership."""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from persistence.uow import UnitOfWork


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

    def enqueue_work_event(self, uow, outbox_event_id, account_id, business_key, work_id, payload):
        uow.execute(
            "INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,"
            "work_id,aggregate_type,aggregate_id,payload) VALUES (%s,%s,'publish_work_event',"
            "%s,%s,'work',%s,%s::jsonb)",
            (outbox_event_id, account_id, business_key, work_id, work_id, payload),
        )
