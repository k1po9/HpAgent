"""SQL for mandate identity, immutable requirements and business events."""

from __future__ import annotations

from uuid import UUID

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound


class WorkRepository:
    @staticmethod
    def get(uow: UnitOfWork, account_id: UUID, work_id: UUID, *, lock: bool = False) -> dict:
        row = uow.execute(
            "SELECT * FROM works WHERE account_id=%s AND work_id=%s"
            + (" FOR UPDATE" if lock else ""),
            (account_id, work_id),
        ).fetchone()
        if row is None:
            raise ResourceNotFound()
        return dict(row)

    @staticmethod
    def set_continuation(uow, continuation_json, work_id):
        uow.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s",
            (continuation_json, work_id),
        )

    @staticmethod
    def finish_coordinator(
        uow,
        status,
        continuation_json,
        checkpoint_json,
        epoch_status,
        stopped_status,
        work_id,
        run_id,
    ):
        return uow.execute(
            "UPDATE works SET active_coordinator_run_id=NULL,status=%s,continuation=%s::jsonb,checkpoint=%s::jsonb,row_version=row_version+1,control_epoch=control_epoch+CASE WHEN %s IN ('paused','stopped') THEN 1 ELSE 0 END,stopped_at=CASE WHEN %s='stopped' THEN GREATEST(now(),created_at,updated_at) ELSE NULL END,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s AND active_coordinator_run_id=%s RETURNING *",
            (
                status,
                continuation_json,
                checkpoint_json,
                epoch_status,
                stopped_status,
                work_id,
                run_id,
            ),
        ).fetchone()

    @staticmethod
    def set_control_state(uow, status, continuation_json, stopped_status, work_id):
        return uow.execute(
            "UPDATE works SET status=%s,control_epoch=control_epoch+1,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),continuation=%s::jsonb,stopped_at=CASE WHEN %s='stopped' THEN GREATEST(now(),created_at,updated_at) ELSE NULL END WHERE work_id=%s RETURNING *",
            (status, continuation_json, stopped_status, work_id),
        ).fetchone()

    @staticmethod
    def touch(uow, work_id):
        uow.execute(
            "UPDATE works SET row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s",
            (work_id,),
        )

    @staticmethod
    def set_pending_continuation(uow, continuation_json, work_id):
        uow.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1 WHERE work_id=%s",
            (continuation_json, work_id),
        )

    @staticmethod
    def set_continuation_returning(uow, continuation_json, work_id):
        return uow.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s RETURNING *",
            (continuation_json, work_id),
        ).fetchone()

    @staticmethod
    def resolve_control(uow, status, continuation_json, stopped_status, work_id):
        uow.execute(
            "UPDATE works SET status=%s,control_epoch=control_epoch+1,continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),stopped_at=CASE WHEN %s='stopped' THEN GREATEST(now(),created_at,updated_at) ELSE NULL END WHERE work_id=%s",
            (status, continuation_json, stopped_status, work_id),
        )

    @staticmethod
    def set_save_wait(uow, continuation_json, account_id, work_id):
        return uow.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE account_id=%s AND work_id=%s RETURNING *",
            (continuation_json, account_id, work_id),
        ).fetchone()

    @staticmethod
    def revise(uow, revision, checkpoint_json, continuation_json, work_id):
        return uow.execute(
            "UPDATE works SET current_requirement_revision=%s,control_epoch=control_epoch+1,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),checkpoint=%s::jsonb,continuation=%s::jsonb WHERE work_id=%s RETURNING *",
            (revision, checkpoint_json, continuation_json, work_id),
        ).fetchone()

    @staticmethod
    def touch_returning(uow, work_id):
        return uow.execute(
            "UPDATE works SET row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at) WHERE work_id=%s RETURNING *",
            (work_id,),
        ).fetchone()

    @staticmethod
    def complete(uow, revision, receipt_json, account_id, work_id):
        return uow.execute(
            'UPDATE works SET status=\'completed\',completed_requirement_revision=%s,completion_receipt=%s::jsonb,completed_at=GREATEST(now(),created_at,updated_at),row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),continuation=\'{"schema_version":1,"kind":"none","reason":"criteria_satisfied"}\' WHERE account_id=%s AND work_id=%s RETURNING *',
            (revision, receipt_json, account_id, work_id),
        ).fetchone()

    @staticmethod
    def insert(uow, work_id, account_id, title, continuation_json):
        uow.execute(
            "INSERT INTO works(work_id,account_id,title,continuation) VALUES (%s,%s,%s,%s::jsonb)",
            (work_id, account_id, title, continuation_json),
        )

    @staticmethod
    def set_schedule_ready(uow, continuation_json, work_id):
        uow.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s",
            (continuation_json, work_id),
        )

    @staticmethod
    def assign_coordinator(uow, run_id, epoch, continuation_json, work_id):
        uow.execute(
            "UPDATE works SET active_coordinator_run_id=%s,control_epoch=%s,row_version=row_version+1,updated_at=GREATEST(now(),created_at,updated_at),continuation=%s::jsonb WHERE work_id=%s",
            (run_id, epoch, continuation_json, work_id),
        )

    @staticmethod
    def lock_accept_limit(uow, account_key):
        uow.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (account_key,))

    @staticmethod
    def count_accepted(uow, account_id):
        return uow.execute(
            "SELECT count(*) AS n FROM works WHERE account_id=%s AND status NOT IN ('stopped','completed')",
            (account_id,),
        ).fetchone()

    @staticmethod
    def link_conversation(uow, account_id, work_id, conversation_id, command_id, source_message_id):
        uow.execute(
            "INSERT INTO work_conversations(account_id,work_id,conversation_id,linked_by_command_id,source_message_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (account_id, work_id, conversation_id, command_id, source_message_id),
        )

    @staticmethod
    def lock_dispatch_candidates(uow, now, limit):
        return uow.execute(
            "WITH eligible AS (SELECT w.work_id,w.account_id,row_number() OVER (PARTITION BY w.account_id ORDER BY w.work_id) AS tenant_position FROM works w WHERE w.status='active' AND w.active_coordinator_run_id IS NULL AND EXISTS (SELECT 1 FROM work_wakeups x WHERE x.work_id=w.work_id AND x.state='pending' AND x.due_at<=%s)) SELECT w.* FROM eligible e JOIN works w USING(work_id,account_id) LEFT JOIN capacity_turns t ON t.account_id=w.account_id AND t.resource='coordination' AND t.lane='background' ORDER BY e.tenant_position,COALESCE(t.last_admitted_at,'-infinity'::timestamptz),w.work_id LIMIT %s FOR UPDATE OF w SKIP LOCKED",
            (now, limit),
        ).fetchall()
