"""Read-only Research capability queries owned by a fixed Work Run."""

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from work_domain.persistence import dto


class ResearchQueryService:
    def __init__(self, database):
        self.database = database

    @staticmethod
    def _owned(uow, account_id, run_id):
        if not uow.execute("SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s "
                           "AND source_kind='work' AND executor_key='research_report'",
                           (account_id, run_id)).fetchone():
            raise ResourceNotFound()

    def evidence(self, account_id, run_id):
        with UnitOfWork(self.database) as uow:
            self._owned(uow, account_id, run_id)
            rows = uow.execute("SELECT e.*,s.canonical_uri,s.title FROM evidence_items e "
                               "JOIN source_records s ON s.source_id=e.source_id AND s.run_id=e.run_id "
                               "WHERE e.run_id=%s ORDER BY e.created_at,e.evidence_id", (run_id,)).fetchall()
            return {"evidence": dto(rows)}

    def report(self, account_id, run_id):
        with UnitOfWork(self.database) as uow:
            self._owned(uow, account_id, run_id)
            row = uow.execute("SELECT * FROM research_reports WHERE run_id=%s", (run_id,)).fetchone()
            if row is None:
                raise ResourceNotFound()
            return {"report": dto(row)}
