"""SQL for mandate identity, immutable requirements and business events."""

from __future__ import annotations

from typing import cast
from uuid import UUID

from uuid6 import uuid7

from persistence.uow import UnitOfWork


class WakeupRepository:
    @staticmethod
    def enqueue(
        uow: UnitOfWork, work: dict, key: str, kind: str, reason: str, due_at: str | None = None
    ) -> UUID:
        row = uow.execute(
            "INSERT INTO work_wakeups(wakeup_id,account_id,work_id,trigger_key,kind,"
            "expected_revision,reason,due_at) VALUES (%s,%s,%s,%s,%s,%s,%s,COALESCE(%s::timestamptz,now())) "
            "ON CONFLICT(work_id,trigger_key) DO UPDATE SET trigger_key=EXCLUDED.trigger_key "
            "RETURNING wakeup_id,expected_revision,kind,reason",
            (
                uuid7(),
                work["account_id"],
                work["work_id"],
                key,
                kind,
                work["current_requirement_revision"],
                reason,
                due_at,
            ),
        ).fetchone()
        if (row["expected_revision"], row["kind"], row["reason"]) != (
            work["current_requirement_revision"],
            kind,
            reason,
        ):
            from web_domain.errors import IdempotencyConflict

            raise IdempotencyConflict()
        return cast(UUID, row["wakeup_id"])

    @staticmethod
    def supersede_pending(uow, work_id):
        uow.execute(
            "UPDATE work_wakeups SET state='superseded' WHERE work_id=%s AND state='pending'",
            (work_id,),
        )

    @staticmethod
    def skip_pending_due(uow, account_id, work_id):
        uow.execute(
            "UPDATE work_wakeups SET state='skipped' WHERE account_id=%s AND work_id=%s AND kind='due' AND state='pending'",
            (account_id, work_id),
        )

    @staticmethod
    def consume(uow, run_id, wakeup_id):
        uow.execute(
            "UPDATE work_wakeups SET state='consumed',run_id=%s WHERE wakeup_id=%s AND state='pending'",
            (run_id, wakeup_id),
        )

    @staticmethod
    def supersede_revision(uow, work_id, revision):
        uow.execute(
            "UPDATE work_wakeups SET state='superseded' WHERE work_id=%s AND state='pending' AND expected_revision<=%s",
            (work_id, revision),
        )

    @staticmethod
    def lock_owned(uow, account_id, work_id, wakeup_id):
        return uow.execute(
            "SELECT * FROM work_wakeups WHERE account_id=%s AND work_id=%s AND wakeup_id=%s FOR UPDATE",
            (account_id, work_id, wakeup_id),
        ).fetchone()

    @staticmethod
    def kind(uow, wakeup_id):
        return uow.execute(
            "SELECT kind FROM work_wakeups WHERE wakeup_id=%s", (wakeup_id,)
        ).fetchone()

    @staticmethod
    def latest_due(uow, work_id, revision, now):
        return uow.execute(
            "SELECT * FROM work_wakeups WHERE work_id=%s AND expected_revision=%s AND state='pending' AND due_at<=%s ORDER BY due_at DESC,created_at DESC,wakeup_id DESC LIMIT 1 FOR UPDATE",
            (work_id, revision, now),
        ).fetchone()
