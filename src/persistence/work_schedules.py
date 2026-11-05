"""SQL for Work schedule projections and occurrence facts; no timing decisions."""


class WorkScheduleRepository:
    @staticmethod
    def upsert(
        uow,
        schedule_id,
        account_id,
        work_id,
        requirement_revision,
        version,
        enabled,
        timing_json,
        timezone,
        due_at,
        row_version,
    ):
        uow.execute(
            "INSERT INTO work_schedules(schedule_id,account_id,work_id,requirement_revision,schedule_version,desired_enabled,timing,timezone,next_due_at,source_row_version) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s) ON CONFLICT(account_id,work_id) DO UPDATE SET requirement_revision=EXCLUDED.requirement_revision,schedule_version=EXCLUDED.schedule_version,desired_enabled=EXCLUDED.desired_enabled,timing=EXCLUDED.timing,timezone=EXCLUDED.timezone,next_due_at=EXCLUDED.next_due_at,source_row_version=EXCLUDED.source_row_version",
            (
                schedule_id,
                account_id,
                work_id,
                requirement_revision,
                version,
                enabled,
                timing_json,
                timezone,
                due_at,
                row_version,
            ),
        )

    @staticmethod
    def update_due(uow, due_at, now, schedule_id):
        uow.execute(
            "UPDATE work_schedules SET next_due_at=%s,last_evaluated_at=%s WHERE schedule_id=%s",
            (due_at, now, schedule_id),
        )

    @staticmethod
    def append_occurrence(
        uow,
        account_id,
        work_id,
        schedule_id,
        version,
        scheduled_for,
        wakeup_id,
        disposition,
        reason,
        missed_from,
    ):
        return uow.execute(
            "INSERT INTO work_schedule_occurrences(account_id,work_id,schedule_id,schedule_version,scheduled_for,wakeup_id,disposition,reason,missed_from) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
            (
                account_id,
                work_id,
                schedule_id,
                version,
                scheduled_for,
                wakeup_id,
                disposition,
                reason,
                missed_from,
            ),
        ).fetchone()

    @staticmethod
    def mark_applied(uow, work_id):
        uow.execute(
            "UPDATE work_schedules SET applied_version=schedule_version WHERE work_id=%s",
            (work_id,),
        )

    @staticmethod
    def lock_owned(uow, account_id, work_id):
        return uow.execute(
            "SELECT * FROM work_schedules WHERE account_id=%s AND work_id=%s FOR UPDATE",
            (account_id, work_id),
        ).fetchone()

    @staticmethod
    def occurrence(uow, schedule_id, version, scheduled_for):
        return uow.execute(
            "SELECT * FROM work_schedule_occurrences WHERE schedule_id=%s AND schedule_version=%s AND scheduled_for=%s",
            (schedule_id, version, scheduled_for),
        ).fetchone()

    @staticmethod
    def lock_unsynchronized(uow, limit):
        return uow.execute(
            "SELECT w.* FROM works w LEFT JOIN work_schedules s USING(account_id,work_id) WHERE s.schedule_id IS NULL OR s.source_row_version<>w.row_version OR s.applied_version<>s.schedule_version ORDER BY w.work_id LIMIT %s FOR UPDATE OF w SKIP LOCKED",
            (limit,),
        ).fetchall()

    @staticmethod
    def lock_due(uow, now, limit):
        return uow.execute(
            "SELECT w.* FROM works w JOIN work_schedules s USING(account_id,work_id) WHERE s.desired_enabled AND s.next_due_at<=%s ORDER BY s.next_due_at LIMIT %s FOR UPDATE OF w SKIP LOCKED",
            (now, limit),
        ).fetchall()

    @staticmethod
    def lock_for_work(uow, work_id):
        return uow.execute(
            "SELECT * FROM work_schedules WHERE work_id=%s FOR UPDATE", (work_id,)
        ).fetchone()
