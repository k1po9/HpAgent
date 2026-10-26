"""Read-only cutover data checks; process shutdown remains an operator action."""
from __future__ import annotations

from collections import Counter

from application.memory_governance import as_dict
from memory.hindsight_client import HindsightContractError
from memory.scope_policy import MemoryScope
from persistence.migrate import verify_schema
from persistence.uow import UnitOfWork


async def pages(fetch, field, **kwargs):
    """Browse without search/time filters, whose totals may be estimates."""
    offset = 0
    while True:
        result = as_dict(await fetch(limit=100, offset=offset, **kwargs))
        items = result[field]
        for item in items:
            yield as_dict(item)
        offset += len(items)
        if offset >= result['total'] and not result.get('total_is_estimate'):
            break
        if not items:
            raise HindsightContractError('memory_inventory_incomplete')


def local_inventory(database):
    verify_schema(database)
    with UnitOfWork(database) as uow:
        uow.execute('SET TRANSACTION READ ONLY')
        sources = uow.execute(
            'SELECT s.account_id,s.document_id,s.scope,s.state,s.policy_version,s.generation,'
            'a.generation AS action_generation,a.status,r.memory_retention_blocked FROM memory_sources s '
            'JOIN runs r ON r.account_id=s.account_id AND r.run_id=s.origin_run_id '
            'LEFT JOIN memory_actions a ON a.account_id=s.account_id '
            'AND a.source_id=s.source_id AND a.action_id=s.current_action_id').fetchall()
        actions = uow.execute(
            "SELECT status,count(*) AS count FROM memory_actions WHERE status<>'verified' "
            "OR send_state IN ('sending','unknown') "
            'GROUP BY status').fetchall()
        events = uow.execute(
            "SELECT status,count(*) AS count FROM outbox_events "
            "WHERE event_type IN ('retain_memory','memory_action') AND status<>'processed' "
            'GROUP BY status').fetchall()
    return sources, {r['status']: r['count'] for r in actions}, {
        r['status']: r['count'] for r in events}


async def inspect_memory(database, remote, *, require_empty=False):
    # No request/operation payloads or document text enter the report.
    import asyncio

    sources, actions, events = await asyncio.to_thread(local_inventory, database)
    registered = {('hpagent-u-' + str(s['account_id']), s['document_id']): s for s in sources}
    seen = set()
    problems: Counter = Counter()
    banks = documents = inflight = 0
    async with remote._sdk() as sdk:
        version = await sdk.aget_version()
        if version.api_version != '1.1.0':
            raise HindsightContractError('memory_target_version_mismatch')
        async for bank in pages(sdk.banks.list_banks, 'banks'):
            banks += 1
            bank_id = bank['bank_id']
            for status in ('pending', 'processing', 'failed'):
                result = as_dict(await sdk.operations.list_operations(
                    bank_id, status=status, limit=1, offset=0))
                inflight += result['total']
            async for document in pages(sdk.documents.list_documents, 'items', bank_id=bank_id):
                documents += 1
                key = (bank_id, document['id'])
                seen.add(key)
                source = registered.get(key)
                if source is None:
                    # Absence from the source allowlist isolates facts AND derived
                    # observations. No default-private mapping or fake Run.
                    problems['unregistered_isolated'] += 1
                    continue
                if source['state'] in {'blocked', 'quarantined', 'forgotten'}:
                    problems['source_not_active'] += 1
                    continue
                if source['memory_retention_blocked']:
                    problems['source_permission_revoked'] += 1
                    continue
                if (source['policy_version'] != 'chat-v2' or source['status'] != 'verified'
                        or source['generation'] != source['action_generation']):
                    problems['source_unverified'] += 1
                    continue
                detail = as_dict(await remote.get_document(str(source['account_id']), key[1]))
                if (detail.get('bank_id') != bank_id or detail.get('id') != key[1]
                        or not MemoryScope(str(source['account_id']), tuple(source['scope'])).matches_document(detail)):
                    problems['scope_mismatch'] += 1
        for key, source in registered.items():
            if key not in seen and source['state'] != 'forgotten':
                problems['registered_document_missing'] += 1
    if require_empty and (documents or sources):
        problems['empty_target_required'] += 1
    return {'service_version': version.api_version, 'banks': banks, 'documents': documents,
            'sources': len(sources), 'actions_requiring_reconciliation': actions,
            'outbox_requiring_reconciliation': events, 'remote_operations_requiring_reconciliation': inflight,
            'problems': dict(problems), 'require_empty': require_empty,
            'data_ready': not (problems or actions or events or inflight),
            'external_checks': ['stop and identify every old sender',
                                'settle old synchronous HTTP requests before enabling new senders'],
            'business_switch_executed': False}
