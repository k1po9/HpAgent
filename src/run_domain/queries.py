"""Owner-facing capability projections; never used as execution authorization."""

from persistence.uow import UnitOfWork
from web_domain.errors import ResourceNotFound
from work_domain.persistence import dto


def capability_results(database, account_id, run_id):
    with UnitOfWork(database) as uow:
        if not uow.execute(
            'SELECT 1 FROM runs WHERE account_id=%s AND run_id=%s', (account_id, run_id)
        ).fetchone():
            raise ResourceNotFound()
        rows = uow.execute(
            'SELECT i.capability_key,r.invocation_id,r.payload,r.created_at '
            'FROM capability_results r JOIN capability_invocations i USING(account_id,invocation_id) '
            'WHERE r.account_id=%s AND r.run_id=%s '
            'ORDER BY r.created_at DESC,r.invocation_id DESC LIMIT 101',
            (account_id, run_id),
        ).fetchall()
        return {'items': dto(rows[:100]), 'truncated': len(rows) > 100}
