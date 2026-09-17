from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from uuid import UUID

from persistence.uow import UnitOfWork


class ArtifactOutboxService:
    def __init__(self, database: object):
        self.database = database

    def claim(self, worker_id: str, limit: int = 10):
        with UnitOfWork(self.database) as uow:
            return list(uow.execute(
                "WITH candidates AS (SELECT artifact_outbox_event_id FROM artifact_outbox_events "
                "WHERE status='pending' AND available_at<=now() ORDER BY available_at,created_at "
                "FOR UPDATE SKIP LOCKED LIMIT %s) UPDATE artifact_outbox_events o SET "
                "status='processing',attempt_count=attempt_count+1,locked_at=now(),locked_by=%s,"
                "updated_at=now() FROM candidates c WHERE o.artifact_outbox_event_id="
                "c.artifact_outbox_event_id RETURNING o.*", (limit, worker_id),
            ).fetchall())

    def mark_processed(self, event_id: UUID, worker_id: str) -> bool:
        with UnitOfWork(self.database) as uow:
            return uow.execute(
                "UPDATE artifact_outbox_events SET status='processed',processed_at=now(),"
                "locked_at=NULL,locked_by=NULL,updated_at=now() WHERE artifact_outbox_event_id=%s "
                "AND status='processing' AND locked_by=%s RETURNING artifact_outbox_event_id",
                (event_id, worker_id),
            ).fetchone() is not None

    def retry(self, event_id: UUID, worker_id: str, code: str, message: str,
              available_at: datetime) -> bool:
        with UnitOfWork(self.database) as uow:
            return uow.execute(
                "UPDATE artifact_outbox_events SET status='pending',available_at=%s,"
                "last_error_code=%s,last_error_message=%s,locked_at=NULL,locked_by=NULL,"
                "updated_at=now() WHERE artifact_outbox_event_id=%s AND status='processing' "
                "AND locked_by=%s RETURNING artifact_outbox_event_id",
                (available_at, code, message, event_id, worker_id),
            ).fetchone() is not None

    def dead_letter(self, event_id: UUID, worker_id: str, code: str, message: str) -> bool:
        with UnitOfWork(self.database) as uow:
            return uow.execute(
                "UPDATE artifact_outbox_events SET status='dead_letter',last_error_code=%s,"
                "last_error_message=%s,locked_at=NULL,locked_by=NULL,updated_at=now() "
                "WHERE artifact_outbox_event_id=%s AND status='processing' AND locked_by=%s "
                "RETURNING artifact_outbox_event_id", (code, message, event_id, worker_id),
            ).fetchone() is not None

    def fail_version(self, version_id: UUID, code: str, message: str) -> None:
        with UnitOfWork(self.database) as uow:
            uow.execute(
                "UPDATE artifact_versions SET status='failed',html=NULL,failure_code=%s,"
                "failure_message=%s,started_at=COALESCE(started_at,now()),completed_at=now(),"
                "updated_at=now() WHERE artifact_version_id=%s "
                "AND status IN ('queued','running')",
                (code, message[:1000], version_id),
            )

    def recover_expired(self, older_than_seconds: int,
                        event_types: Collection[str] | None = None) -> int:
        with UnitOfWork(self.database) as uow:
            cursor = uow.execute(
                "UPDATE artifact_outbox_events SET status='pending',locked_at=NULL,locked_by=NULL,"
                "updated_at=now() WHERE status='processing' AND locked_at < "
                "now()-(%s * interval '1 second')", (older_than_seconds,),
            )
            return int(cursor.rowcount)
