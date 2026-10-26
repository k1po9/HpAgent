"""Scope invalidation on the caller's transaction; never stores memory text."""
from __future__ import annotations

from psycopg.types.json import Jsonb

from resources.model_governance_errors import ModelGovernanceError


class MemoryContextRevoked(ModelGovernanceError):
    code = 'memory_context_revoked'
    safe_message = '长期记忆上下文已撤回，请开始新的执行。'


def account_epoch(uow, account_id):
    row = uow.execute('SELECT memory_epoch FROM accounts WHERE account_id=%s FOR UPDATE',
                      (account_id,)).fetchone()
    if row is None:
        from web_domain.errors import ResourceNotFound
        raise ResourceNotFound()
    return row['memory_epoch']


def intersects(scopes, affected):
    return (scopes is None or affected is None or affected == 'account' or
            any(scope in affected for scope in scopes))


def check_manifest(uow, account_id, manifest, *, epoch=None):
    epoch = account_epoch(uow, account_id) if epoch is None else epoch
    # A missing legacy manifest cannot prove absence of inherited memory.
    if manifest is None:
        if epoch:
            raise MemoryContextRevoked()
        return None
    checked = manifest['epoch']
    if checked > epoch:
        raise MemoryContextRevoked()
    if manifest['scopes'] == []:
        return {**manifest, 'epoch': epoch}
    rows = uow.execute('SELECT invalidation_epoch,affected_scopes FROM memory_actions '
                       'WHERE account_id=%s AND invalidation_epoch>%s ORDER BY invalidation_epoch',
                       (account_id, checked)).fetchall()
    if len(rows) != epoch - checked or any(r['invalidation_epoch'] != checked + i + 1 for i, r in enumerate(rows)):
        raise MemoryContextRevoked()
    if manifest['scopes'] != [] and any(intersects(manifest['scopes'], r['affected_scopes']) for r in rows):
        raise MemoryContextRevoked()
    return {**manifest, 'epoch': epoch}


def check_execution(uow, account_id, run_id, execution_id=None, *, advance=True):
    epoch = account_epoch(uow, account_id)
    row = uow.execute('SELECT execution_id,memory_context FROM run_executions '
                      "WHERE account_id=%s AND run_id=%s AND ((%s::uuid IS NULL AND role='root') "
                      'OR execution_id=%s)' + (' FOR UPDATE' if advance else ''),
                      (account_id, run_id, execution_id, execution_id)).fetchone()
    if row is None:
        return None
    manifest = check_manifest(uow, account_id, row['memory_context'], epoch=epoch)
    if advance and manifest != row['memory_context']:
        uow.execute("UPDATE run_executions SET memory_context=%s WHERE execution_id=%s AND status IN ('queued','running')",
                    (Jsonb(manifest), row['execution_id']))
    return manifest


def bind_context(uow, account_id, execution_id, scopes, documents, epoch):
    manifest = {'epoch': epoch, 'scopes': scopes, 'documents': sorted(documents)}
    check_manifest(uow, account_id, manifest)
    uow.execute('UPDATE run_executions SET memory_context=%s WHERE account_id=%s AND execution_id=%s',
                (Jsonb(manifest), account_id, execution_id))


def record_revocation(uow, account_id, source, reason, scopes):
    """Permission maintenance records one scoped event alongside its business fact."""
    # Deletion already blocks and invalidates this source. Keep its permanent
    # tombstone and current cleanup action; a permission denial must not cancel
    # an accepted deletion or make its remote reconciliation unreachable.
    if source['state'] == 'forgotten':
        return
    from uuid import uuid4

    from persistence.memory import request_hash

    aid = uuid4()
    epoch = account_epoch(uow, account_id) + 1
    uow.execute('UPDATE accounts SET memory_epoch=%s WHERE account_id=%s', (epoch, account_id))
    uow.execute('INSERT INTO memory_actions(action_id,account_id,source_id,generation,kind,actor,reason,'
                'action_key,request_hash,invalidation_epoch,affected_scopes,status,verified_at) '
                "VALUES (%s,%s,%s,%s,'invalidate','system',%s,%s,%s,%s,%s,'verified',now())",
                (aid, account_id, source['source_id'], source['generation'] + 1, reason, str(aid),
                 request_hash({'reason': reason}), epoch, Jsonb(scopes)))
    uow.execute("UPDATE memory_sources SET state='quarantined',manual_curation=true,generation=generation+1,"
                'current_action_id=%s,updated_at=now() WHERE account_id=%s AND source_id=%s',
                (aid, account_id, source['source_id']))
    uow.execute('UPDATE memory_actions SET verification=%s WHERE account_id=%s AND action_id=%s',
                (Jsonb({'permission_revoked': True, 'source_quarantined': True}), account_id, aid))
