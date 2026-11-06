"""One command boundary shared by API and application tools."""

from __future__ import annotations

from uuid import UUID

from persistence.uow import UnitOfWork
from persistence.work_requirements import WorkRequirementRepository
from persistence.works import WorkRepository
from work_domain.serialization import to_json_value


class WorkQueries:
    def __init__(self, database: object, repo: WorkRepository):
        self.database = database
        self.repo = repo

    def snapshot_in_uow(self, uow, account_id, work_id):
        work = self.repo.get(uow, account_id, work_id)
        work["requirement"] = WorkRequirementRepository.get(
            uow, account_id, work_id, work["current_requirement_revision"]
        )
        schedule = uow.execute(
            "SELECT * FROM work_schedules WHERE account_id=%s AND work_id=%s", (account_id, work_id)
        ).fetchone()
        work["schedule"] = dict(schedule) if schedule else None
        progress = uow.execute(
            "SELECT policy_version,failure_limit,no_progress_limit,consecutive_failures,"
            "no_progress_count,blocked_reason FROM work_progress WHERE account_id=%s "
            "AND work_id=%s AND requirement_revision=%s",
            (account_id, work_id, work["current_requirement_revision"]),
        ).fetchone()
        work["progress"] = dict(progress) if progress else None
        from work_domain.progress import previous_daily

        baseline = previous_daily(uow, work)
        work["daily_baseline"] = (
            {k: baseline[k] for k in ("run_id", "previous_run_id", "changed")} if baseline else None
        )
        from work_domain.integration import projections

        work.update(projections(uow, account_id, work_id))
        work["conversation_ids"] = [
            row["conversation_id"]
            for row in uow.execute(
                "SELECT conversation_id FROM work_conversations WHERE account_id=%s AND work_id=%s "
                "ORDER BY conversation_id",
                (account_id, work_id),
            ).fetchall()
        ]
        # Exact, durable message anchors; linking alone never invents a source.
        work["sources"] = [
            dict(row)
            for row in uow.execute(
                "SELECT wc.conversation_id,m.message_id AS source_message_id "
                "FROM work_conversations wc JOIN conversations c USING(account_id,conversation_id) "
                "LEFT JOIN messages m ON m.account_id=wc.account_id "
                "AND m.conversation_id=wc.conversation_id AND m.message_id=wc.source_message_id "
                "WHERE wc.account_id=%s AND wc.work_id=%s ORDER BY wc.created_at,wc.conversation_id",
                (account_id, work_id),
            ).fetchall()
        ]
        return to_json_value(work)

    def get(self, account_id: UUID, work_id: UUID) -> dict:
        with UnitOfWork(self.database) as uow:
            return {"work": self.snapshot_in_uow(uow, account_id, work_id)}

    def list(self, account_id: UUID, before: UUID | None = None) -> dict:
        with UnitOfWork(self.database) as uow:
            rows = uow.execute(
                "SELECT * FROM works WHERE account_id=%s AND "
                "(%s::uuid IS NULL OR work_id<%s) ORDER BY work_id DESC LIMIT 51",
                (account_id, before, before),
            ).fetchall()
            return {
                "items": [
                    self.snapshot_in_uow(uow, account_id, row["work_id"]) for row in rows[:50]
                ],
                "next_before": str(rows[49]["work_id"]) if len(rows) > 50 else None,
            }

    def records(self, account_id, work_id, kind, after=0):
        with UnitOfWork(self.database) as uow:
            self.repo.get(uow, account_id, work_id)
            if kind == "events":
                rows = uow.execute(
                    "SELECT * FROM work_events WHERE account_id=%s AND work_id=%s "
                    "AND event_seq>%s ORDER BY event_seq LIMIT 100",
                    (account_id, work_id, after),
                ).fetchall()
            elif kind == "runs":
                rows = uow.execute(
                    "SELECT * FROM runs WHERE account_id=%s AND work_id=%s "
                    "ORDER BY run_id DESC LIMIT 100",
                    (account_id, work_id),
                ).fetchall()
            else:
                raise ValueError("unsupported records")
            return {"items": to_json_value(rows)}
