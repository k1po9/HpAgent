"""Account-owned, instance-addressed Research queries; Run is attribution only."""

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from work_domain.persistence import dto


class ResearchQueryService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _owned(uow, account_id, run_id, invocation_id=None):
        run = uow.execute(
            "SELECT executor_key FROM runs WHERE account_id=%s AND run_id=%s", (account_id, run_id)
        ).fetchone()
        if run is None:
            raise ResourceNotFound()
        if (
            invocation_id is not None
            and not uow.execute(
                "SELECT 1 FROM capability_invocations WHERE account_id=%s AND run_id=%s "
                "AND invocation_id=%s AND capability_key='research'",
                (account_id, run_id, invocation_id),
            ).fetchone()
        ):
            raise ResourceNotFound()
        if invocation_id is not None:
            return invocation_id
        if run["executor_key"] != "research_report":
            raise ResourceNotFound()
        key = f"research:{run_id}:invoke:v1"
        row = uow.execute(
            "SELECT 1 FROM capability_invocations WHERE account_id=%s AND run_id=%s AND invocation_id=%s",
            (account_id, run_id, key),
        ).fetchone()
        return key if row else str(run_id)

    def evidence(self, account_id, run_id, invocation_id=None):
        with UnitOfWork(self.database) as uow:
            key = self._owned(uow, account_id, run_id, invocation_id)
            rows = uow.execute(
                "SELECT e.*,s.canonical_uri,s.title FROM evidence_items e "
                "JOIN source_records s ON s.source_id=e.source_id AND s.research_id=e.research_id "
                "WHERE e.research_id=%s AND e.run_id=%s ORDER BY e.created_at,e.evidence_id",
                (key, run_id),
            ).fetchall()
            return {"evidence": dto(rows)}

    def report(self, account_id, run_id, invocation_id=None):
        with UnitOfWork(self.database) as uow:
            key = self._owned(uow, account_id, run_id, invocation_id)
            row = uow.execute(
                "SELECT * FROM research_reports WHERE research_id=%s AND run_id=%s", (key, run_id)
            ).fetchone()
            if row is None:
                raise ResourceNotFound()
            result = uow.execute(
                "SELECT payload FROM capability_results WHERE account_id=%s AND run_id=%s AND invocation_id=%s",
                (account_id, run_id, row["invocation_id"]),
            ).fetchone()
            return {
                "report": dto(row),
                "capability_result": dto(result["payload"]) if result else None,
            }
