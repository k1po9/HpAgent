"""Source governance commands and Hindsight reconciliation outside transactions."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from psycopg.types.json import Jsonb

from memory.hindsight_client import HindsightContractError, HindsightRejected
from memory.scope_policy import MemoryScope, chat_scope
from persistence.command_result import CommandResult
from persistence.memory import MemoryStore, request_hash
from persistence.memory_context import account_epoch
from persistence.repositories import IdempotencyRepository
from persistence.uow import UnitOfWork, retryable_transaction
from web_domain.errors import (
    ConversationBusy,
    IdempotencyConflict,
    ResourceNotFound,
    VersionConflict,
)

ACTION_COLUMNS = ('action_id,source_id,generation,kind,status,error_code,verification,'
                  'invalidation_epoch,affected_scopes,created_at,verified_at')


def dto(value):
    return json.loads(json.dumps(value, default=str))


def as_dict(value):
    return value if isinstance(value, dict) else value.to_dict()


def authorize(uow, account, source_id):
    source = uow.execute('SELECT s.*,r.conversation_id,m.origin FROM memory_sources s '
        'JOIN runs r ON r.account_id=s.account_id AND r.run_id=s.origin_run_id '
        'JOIN messages m ON m.account_id=r.account_id AND m.message_id=r.trigger_message_id '
        'JOIN conversations c ON c.account_id=r.account_id AND c.conversation_id=r.conversation_id '
        "JOIN accounts a ON a.account_id=s.account_id AND a.status='active' "
        "WHERE s.account_id=%s AND s.source_id=%s FOR UPDATE OF s",
        (account, source_id)).fetchone()
    if source is None or source['source_kind'] != 'chat':
        raise ResourceNotFound()
    if list(chat_scope(str(account), source['origin'] or {}).tags) != source['scope']:
        raise ResourceNotFound()
    return dict(source)


def invalidate(uow, account, action_id, scopes):
    epoch = uow.execute('UPDATE accounts SET memory_epoch=memory_epoch+1 WHERE account_id=%s '
                        'RETURNING memory_epoch', (account,)).fetchone()['memory_epoch']
    uow.execute('UPDATE memory_actions SET invalidation_epoch=%s,affected_scopes=%s '
                'WHERE account_id=%s AND action_id=%s', (epoch, Jsonb(scopes), account, action_id))


class MemoryGovernanceService:
    def __init__(self, database, remote=None):
        self.remote = remote
        self.database = database
        self.store = MemoryStore(database)

    def sources(self, account, limit=50, offset=0):
        with UnitOfWork(self.database) as uow:
            ids = uow.execute('SELECT source_id FROM memory_sources WHERE account_id=%s '
                               'ORDER BY created_at DESC,source_id DESC LIMIT %s OFFSET %s', (account, limit, offset)).fetchall()
            result = []
            for row in ids:
                try:
                    s = authorize(uow, account, row['source_id'])
                except ResourceNotFound:
                    continue
                result.append(self._source(s))
            return {'sources': result, 'next_offset': offset + len(ids) if len(ids) == limit else None}

    async def detail(self, account, source_id):
        from memory.hindsight_client import HindsightError

        result = await asyncio.to_thread(self.source, account, source_id)
        result['facts_available'] = False
        if self.remote is None or result['source']['state'] in {'forgotten', 'quarantined'}:
            return result
        try:
            facts = await self.remote.list_facts(str(account), document_id=result['source']['document_id'], state='all')
        except HindsightError:
            result['facts_error'] = 'memory_backend_unavailable'
            return result
        current = await asyncio.to_thread(self.source, account, source_id)
        if current['source']['generation'] != result['source']['generation']:
            raise VersionConflict(current['source']['generation'])
        result['facts'] = [f for f in facts if set(f.get('tags') or []) == set(result['source']['scope'])]
        result['facts_available'] = True
        return result

    async def validate_target(self, account, source_id, key, payload):
        if self.remote is None or payload['kind'] == 'delete':
            return  # The worker always performs the same validation again.
        with UnitOfWork(self.database) as uow:
            source = authorize(uow, account, source_id)
            if source['state'] == 'quarantined':
                raise ConversationBusy()
            if uow.execute('SELECT 1 FROM memory_actions WHERE account_id=%s AND action_key=%s',
                           (account, 'command:' + key)).fetchone():
                return
        fact = as_dict(await self.remote.get_fact(str(account), payload['memory_id']))
        if (fact.get('document_id') != source['document_id'] or
                set(fact.get('tags') or []) != set(source['scope']) or
                fact.get('type', fact.get('fact_type')) not in {'world', 'experience'}):
            raise HindsightRejected('memory_target_scope_denied', 403)

    @staticmethod
    def _source(s):
        return dto({k: s[k] for k in ('source_id', 'document_id', 'scope', 'generation', 'state',
                                      'manual_curation', 'current_action_id')})

    def source(self, account, source_id):
        with UnitOfWork(self.database) as uow:
            s = authorize(uow, account, source_id)
            actions = uow.execute(f'SELECT {ACTION_COLUMNS} FROM memory_actions WHERE account_id=%s '
                                   'AND source_id=%s ORDER BY created_at', (account, source_id)).fetchall()
            promotions = uow.execute('WITH RECURSIVE copies AS ('
                'SELECT source_id,document_id,scope,state,current_action_id FROM memory_sources '
                'WHERE account_id=%s AND parent_source_id=%s UNION ALL '
                'SELECT c.source_id,c.document_id,c.scope,c.state,c.current_action_id FROM memory_sources c '
                'JOIN copies p ON c.parent_source_id=p.source_id WHERE c.account_id=%s) '
                'SELECT * FROM copies ORDER BY source_id', (account, source_id, account)).fetchall()
            return {'source': self._source(s), 'actions': dto(actions), 'registered_promotions': dto(promotions),
                    'deletion_scope': 'specified_source_only', 'global_semantic_deletion': False}

    def action(self, account, action_id):
        with UnitOfWork(self.database) as uow:
            row = uow.execute(f'SELECT {ACTION_COLUMNS} FROM memory_actions WHERE account_id=%s '
                               'AND action_id=%s', (account, action_id)).fetchone()
            if row is None:
                raise ResourceNotFound()
            authorize(uow, account, row['source_id'])
            return {'action': dto(row), 'deletion_scope': 'specified_source_only',
                    'global_semantic_deletion': False}

    @retryable_transaction
    def command(self, account, source_id, key, payload):
        with UnitOfWork(self.database) as uow:
            if not uow.execute("SELECT 1 FROM accounts WHERE account_id=%s AND status='active'", (account,)).fetchone():
                raise ResourceNotFound()
            account_epoch(uow, account)  # Same ordering as model dispatch admission.
            s = authorize(uow, account, source_id)
            digest = request_hash({'source_id': str(source_id), **payload})
            idem = IdempotencyRepository()
            old = idem.claim(uow, uuid4(), account, 'memory_action', key, bytes.fromhex(digest),
                             datetime.now(UTC) + timedelta(days=7))
            if old is not None:
                if bytes(old['request_hash']).hex() != digest:
                    raise IdempotencyConflict()
                return CommandResult(old['response_status'], old['response_body'], replayed=True)
            # Durable action key also prevents replay after the short receipt expires.
            old_action = uow.execute('SELECT action_id,request_hash,verification FROM memory_actions WHERE '
                                     'account_id=%s AND action_key=%s', (account, 'command:' + key)).fetchone()
            if old_action:
                if old_action['request_hash'] != digest:
                    raise IdempotencyConflict()
                result = {'action_id': str(old_action['action_id']),
                          'source_ids': (old_action['verification'] or {}).get('source_ids', [str(source_id)]),
                          'descendant_actions': (old_action['verification'] or {}).get('descendant_actions', [])}
            else:
                if s['generation'] != payload['generation']:
                    raise VersionConflict(s['generation'])
                if s['state'] == 'forgotten':
                    raise ConversationBusy()
                kind = payload['kind']
                if s['state'] == 'quarantined' and kind != 'delete':
                    raise ConversationBusy()
                current = uow.execute('SELECT status,kind FROM memory_actions WHERE account_id=%s '
                                       'AND action_id=%s', (account, s['current_action_id'])).fetchone()
                if kind != 'delete' and (current is None or current['status'] != 'verified'):
                    raise ConversationBusy()
                # Restore only a fact invalidated by a verified local action.
                if kind == 'restore':
                    previous = uow.execute('SELECT kind,status FROM memory_actions WHERE account_id=%s '
                        'AND source_id=%s AND target_memory_id=%s ORDER BY created_at DESC LIMIT 1',
                        (account, source_id, payload['memory_id'])).fetchone()
                    if previous is None or previous['kind'] != 'invalidate' or previous['status'] != 'verified':
                        raise ConversationBusy()
                aid = uuid4()
                generation = s['generation'] + 1
                uow.execute('INSERT INTO memory_actions(action_id,account_id,source_id,generation,kind,'
                    'actor,reason,action_key,request_hash,request_payload,target_memory_id) '
                    "VALUES (%s,%s,%s,%s,%s,'account',%s,%s,%s,%s,%s)",
                    (aid, account, source_id, generation, kind, payload['reason'], 'command:' + key,
                     digest, Jsonb(payload), payload.get('memory_id')))
                uow.execute('UPDATE memory_sources SET generation=%s,state=%s,manual_curation=true,'
                    'current_action_id=%s,updated_at=now() WHERE account_id=%s AND source_id=%s',
                    (generation, 'forgotten' if kind == 'delete' else 'blocked', aid, account, source_id))
                uow.execute('UPDATE memory_actions SET affected_scopes=%s WHERE account_id=%s AND action_id=%s',
                            (Jsonb([s['scope']]), account, aid))
                source_ids, child_actions, scopes = [str(source_id)], [], [s['scope']]
                unknown_scope = s['policy_version'] != 'chat-v2'
                if kind == 'delete':
                    children = uow.execute('WITH RECURSIVE descendants AS ('
                        'SELECT * FROM memory_sources WHERE account_id=%s AND parent_source_id=%s '
                        'UNION ALL SELECT c.* FROM memory_sources c JOIN descendants p '
                        'ON c.account_id=p.account_id AND c.parent_source_id=p.source_id) '
                        'SELECT * FROM descendants ORDER BY source_id FOR UPDATE', (account, source_id)).fetchall()
                    for child in children:
                        unknown_scope = unknown_scope or child['policy_version'] != 'chat-v2'
                        if child['state'] == 'forgotten':
                            child_actions.append(str(child['current_action_id']))
                            source_ids.append(str(child['source_id']))
                            if child['scope'] not in scopes:
                                scopes.append(child['scope'])
                            continue
                        cid = uuid4()
                        cp = {'kind': 'delete', 'root_source_id': str(source_id)}
                        uow.execute('INSERT INTO memory_actions(action_id,account_id,source_id,generation,kind,'
                            'actor,reason,action_key,request_hash,request_payload,affected_scopes) '
                            "VALUES (%s,%s,%s,%s,'delete','parent_delete',%s,%s,%s,%s,%s)",
                            (cid, account, child['source_id'], child['generation'] + 1, payload['reason'],
                             'command:' + key + ':' + str(child['source_id']), request_hash(cp), Jsonb(cp), Jsonb([child['scope']])))
                        uow.execute("UPDATE memory_sources SET state='forgotten',manual_curation=true,generation=generation+1,"
                            'current_action_id=%s WHERE account_id=%s AND source_id=%s', (cid, account, child['source_id']))
                        conversation = uow.execute('SELECT conversation_id FROM runs WHERE account_id=%s AND run_id=%s',
                                                   (account, child['origin_run_id'])).fetchone()['conversation_id']
                        self.store.outbox.enqueue(uow, uuid4(), account, 'memory_action', 'memory-action:' + str(cid),
                            conversation, child['origin_run_id'], json.dumps({'action_id': str(cid)}))
                        source_ids.append(str(child['source_id']))
                        child_actions.append(str(cid))
                        if child['scope'] not in scopes:
                            scopes.append(child['scope'])
                    evidence = {'source_ids': source_ids, 'descendant_actions': child_actions}
                    uow.execute('UPDATE memory_actions SET verification=%s WHERE account_id=%s AND action_id=%s',
                                (Jsonb(evidence), account, aid))
                if kind != 'restore':
                    invalidate(uow, account, aid, 'account' if unknown_scope else scopes)
                self.store.outbox.enqueue(uow, uuid4(), account, 'memory_action', 'memory-action:' + str(aid),
                    s['conversation_id'], s['origin_run_id'], json.dumps({'action_id': str(aid)}))
                result = {'action_id': str(aid), 'source_ids': source_ids, 'descendant_actions': child_actions}
            result.update(deletion_scope='specified_source_only', global_semantic_deletion=False)
            idem.complete(uow, account, 'memory_action', key, 202, json.dumps(result))
            return CommandResult(202, result)

    @retryable_transaction
    def reconcile_scope(self, account, source_id, generation):
        """Internal source mapping correction: derive destination from persisted origin."""
        with UnitOfWork(self.database) as uow:
            account_epoch(uow, account)
            s = uow.execute('SELECT s.*,r.conversation_id,m.origin FROM memory_sources s '
                'JOIN runs r ON r.account_id=s.account_id AND r.run_id=s.origin_run_id '
                'JOIN messages m ON m.account_id=r.account_id AND m.message_id=r.trigger_message_id '
                'WHERE s.account_id=%s AND s.source_id=%s FOR UPDATE OF s', (account, source_id)).fetchone()
            if s is None or s['source_kind'] != 'chat':
                raise ResourceNotFound()
            destination = list(chat_scope(str(account), s['origin'] or {}).tags)
            if s['generation'] != generation:
                raise VersionConflict(s['generation'])
            if destination == s['scope']:
                return None
            current = uow.execute('SELECT status FROM memory_actions WHERE account_id=%s AND action_id=%s',
                                  (account, s['current_action_id'])).fetchone()
            if s['state'] != 'active' or current is None or current['status'] != 'verified':
                raise ConversationBusy()
            aid = uuid4()
            payload = {'new_scope': destination}
            uow.execute('INSERT INTO memory_actions(action_id,account_id,source_id,generation,kind,actor,reason,'
                'action_key,request_hash,request_payload) '
                "VALUES (%s,%s,%s,%s,'replace','system','source_scope_reconciled',%s,%s,%s)",
                (aid, account, source_id, generation + 1, str(aid), request_hash(payload), Jsonb(payload)))
            uow.execute("UPDATE memory_sources SET generation=generation+1,state='blocked',current_action_id=%s "
                        'WHERE account_id=%s AND source_id=%s', (aid, account, source_id))
            scopes = [s['scope'], destination] if s['policy_version'] == 'chat-v2' else 'account'
            invalidate(uow, account, aid, scopes)
            self.store.outbox.enqueue(uow, uuid4(), account, 'memory_action', 'memory-action:' + str(aid),
                s['conversation_id'], s['origin_run_id'], json.dumps({'action_id': str(aid)}))
            return str(aid)


class MemoryGovernanceWorker:
    def __init__(self, retention):
        self.retention = retention
        self.store = retention.store
        self.remote = retention._hindsight

    async def advance(self, event, worker, action):
        # One source's senders and deletion/payload cleanup cannot overlap.
        async with self.store.sender_guard(action['source_id']) as acquired:
            if not acquired:
                await asyncio.to_thread(self.store.save, event, worker, action, 'uncertain',
                                        error_code='memory_sender_active')
                return
            action = await asyncio.to_thread(self.store.load_action, event, worker)
            if (action['source_generation'] != action['generation'] or
                    action['current_action_id'] != action['action_id']):
                await asyncio.to_thread(self.store.save, event, worker, action,
                                        'uncertain' if action['send_state'] in {'sending', 'unknown'} or action['send_uncertain'] else 'failed',
                                        error_code='memory_source_changed')
                return
            try:
                await asyncio.to_thread(self.check_authority, action)
                if action['kind'] == 'replace':
                    verification = await self.change_scope(action)
                elif action['kind'] == 'delete':
                    verification = await self.delete(event, worker, action)
                else:
                    verification = await self.curate(event, worker, action)
                if verification is not None:
                    await asyncio.to_thread(self.store.save, event, worker, action, 'verified',
                                            verification=verification)
            except HindsightRejected as exc:
                await asyncio.to_thread(self.store.save, event, worker, action, 'failed', error_code=exc.code)
            except Exception as exc:
                # No plaintext or provider error body in the audit.
                code = getattr(exc, 'code', 'memory_verification_unknown')
                waiting = code in {'memory_predecessor_pending', 'memory_descendant_delete_pending', 'memory_old_observation_present'}
                await asyncio.to_thread(self.store.save, event, worker, action, 'verifying' if waiting else 'uncertain',
                                        error_code=code, failure=not waiting)

    async def write(self, action, function, *args, **kwargs):
        if action['send_state'] in {'sending', 'unknown'}:
            await asyncio.to_thread(self.store.send_state, action, 'unknown')
        await asyncio.to_thread(self.store.send_state, action, 'sending')
        try:
            result = await function(*args, **kwargs)
        except HindsightRejected:
            await asyncio.to_thread(self.store.send_state, action, 'settled')
            raise
        except BaseException:
            await asyncio.to_thread(self.store.send_state, action, 'unknown')
            raise
        await asyncio.to_thread(self.store.send_state, action, 'settled')
        return result

    def check_authority(self, action):
        with UnitOfWork(self.store.database_url) as uow:
            if action['actor'] == 'parent_delete':
                authorize(uow, action['account_id'], action['request_payload']['root_source_id'])
            elif action['kind'] == 'replace':
                origin = uow.execute("SELECT m.origin FROM memory_sources s JOIN runs r ON r.account_id=s.account_id AND r.run_id=s.origin_run_id JOIN messages m ON m.account_id=r.account_id AND m.message_id=r.trigger_message_id JOIN accounts a ON a.account_id=s.account_id WHERE s.account_id=%s AND s.source_id=%s AND a.status='active'", (action['account_id'], action['source_id'])).fetchone()
                if origin is None or list(chat_scope(str(action['account_id']), origin['origin'] or {}).tags) != action['request_payload']['new_scope']:
                    raise ResourceNotFound()
            else:
                authorize(uow, action['account_id'], action['source_id'])

    def predecessors(self, action):
        with UnitOfWork(self.store.database_url) as uow:
            return list(uow.execute("SELECT *,created_at<=(SELECT applied_at FROM schema_migrations WHERE version='074_memory_governance.sql') AS legacy_sender FROM memory_actions WHERE account_id=%s AND source_id=%s "
                                    'AND action_id<>%s ORDER BY created_at',
                        (action['account_id'], action['source_id'], action['action_id'])).fetchall())

    async def delete(self, event, worker, action):
        account = str(action['account_id'])
        evidence = action['verification'] or {}
        with UnitOfWork(self.store.database_url) as uow:
            for child in evidence.get('descendant_actions', []):
                state = uow.execute('SELECT status FROM memory_actions WHERE account_id=%s AND action_id=%s',
                                    (action['account_id'], child)).fetchone()
                if state is None or state['status'] != 'verified':
                    raise HindsightContractError('memory_descendant_delete_pending')
        operations = evidence.get('operations', [])
        if not evidence.get('reconciled'):
            predecessors = await asyncio.to_thread(self.predecessors, action)
            for old in predecessors:
                if old['kind'] != 'retain':
                    if old['send_state'] in {'sending', 'unknown'} or old['send_uncertain']:
                        raise HindsightContractError('memory_predecessor_unresolved')
                    if old['status'] not in {'verified', 'failed'}:
                        raise HindsightContractError('memory_predecessor_unresolved')
                    continue
                if old['legacy_sender']:
                    raise HindsightContractError('memory_sender_quiescence_unknown')
                if old['send_state'] == 'idle':
                    continue
                operation = str(old['operation_id'])
                remote = await self.remote.get_operation(account, operation)
                if remote.get('operation_id') != operation:
                    raise HindsightContractError('memory_operation_identity_mismatch')
                if remote['status'] == 'not_found':
                    # Quiescence and identity must both be known before removing dedup.
                    if old['send_state'] in {'sending', 'unknown'}:
                        await asyncio.to_thread(self.store.send_state, old, 'unknown')
                    try:
                        await self.remote.retain_document(**old['request_payload'], async_retain=True, operation_id=operation)
                    except BaseException:
                        await asyncio.to_thread(self.store.send_state, old, 'unknown')
                        raise
                    raise HindsightContractError('memory_predecessor_pending')
                if old['send_state'] != 'settled' or old['send_uncertain']:
                    raise HindsightContractError('memory_sender_quiescence_unknown')
                async def collect(op, status):
                    if status['status'] not in {'completed', 'failed', 'cancelled'}:
                        raise HindsightContractError('memory_predecessor_pending')
                    for child in status.get('child_operations') or []:
                        cid = child.get('operation_id') or child.get('id')
                        if not cid:
                            raise HindsightContractError('memory_child_operation_unknown')
                        await collect(cid, await self.remote.get_operation(account, cid))
                    if op not in operations:
                        operations.append(op)
                await collect(operation, remote)
            try:
                captured = await self.remote.maintenance_evidence(account, action['document_id'], None if action['affected_scopes'] == 'account' else action['scope'])
            except HindsightRejected as exc:
                if exc.status != 404 or operations:
                    raise
                captured = {'fact_ids': [], 'observation_ids': []}
            evidence.update(captured)
            evidence.update(reconciled=True, operations=operations)
            # Persist IDs before removing anything; a crash can resume without re-sending.
            await asyncio.to_thread(self.store.progress, action, evidence)
        present = True
        try:
            await self.remote.get_document(account, action['document_id'])
        except HindsightRejected as exc:
            if exc.status != 404:
                raise
            present = False
        if present:
            try:
                await self.remote.delete_document(account, action['document_id'])
            except HindsightRejected as exc:
                if exc.status != 404:
                    raise
            try:
                await self.remote.get_document(account, action['document_id'])
            except HindsightRejected as exc:
                if exc.status != 404:
                    raise
            else:
                raise HindsightContractError('memory_document_still_present')
        # Native Document cascade is checked through public source/derived reads too.
        try:
            await self.remote.verify_source_absent(account, action['document_id'], None if action['affected_scopes'] == 'account' else action['scope'], evidence)
        except HindsightRejected as exc:
            # A never-sent source may have no remote Bank at all.
            if exc.status != 404 or operations or evidence.get('fact_ids'):
                raise
        for operation in operations:
            if (await self.remote.get_operation(account, operation))['status'] != 'not_found':
                await self.remote.delete_operation(account, operation)
            if (await self.remote.get_operation(account, operation))['status'] != 'not_found':
                raise HindsightContractError('memory_operation_payload_present')
        return {**evidence, 'document_id': action['document_id'], 'deleted': True,
                'operations_cleaned': operations, 'deletion_scope': 'specified_source_only'}

    async def change_scope(self, action):
        account = str(action['account_id'])
        destination = action['request_payload']['new_scope']
        policy = MemoryScope(account, tuple(destination))
        if action['send_uncertain'] or action['send_state'] in {'sending', 'unknown'}:
            document = as_dict(await self.remote.get_document(account, action['document_id']))
            await asyncio.to_thread(self.store.progress, action, {**(action['verification'] or {}),
                'desired_scope_observed': policy.matches_document(document), 'sender_quiescent': False})
            raise HindsightContractError('memory_sender_quiescence_unknown')
        evidence = action['verification']
        if evidence is None:
            evidence = await self.remote.maintenance_evidence(account, action['document_id'], None if action['affected_scopes'] == 'account' else action['scope'])
            await asyncio.to_thread(self.store.progress, action, evidence)
        await self.write(action, self.remote.patch_document_scope, account, action['document_id'],
            scope='private' if destination == ['scope:private'] else 'group',
            group_id=destination[1].removeprefix('group:') if len(destination) == 2 else '')
        document = as_dict(await self.remote.get_document(account, action['document_id']))
        facts = await self.remote.list_facts(account, document_id=action['document_id'], state='all')
        if (not policy.matches_document(document)
                or any(set(f.get('tags') or []) != set(destination) for f in facts)):
            raise HindsightContractError('memory_scope_change_unverified')
        await self.remote.verify_fact_maintenance(account, '', destination, 'scope', evidence)
        return {'scope': destination, 'document_id': action['document_id']}

    async def curate(self, event, worker, action):
        account, target = str(action['account_id']), action['target_memory_id']
        fact = as_dict(await self.remote.get_fact(account, target))
        fact = fact.get('memory', fact)
        if (fact.get('id') != target or fact.get('document_id') != action['document_id'] or fact.get('type', fact.get('fact_type')) not in {'world', 'experience'}
                or set(fact.get('tags') or []) != set(action['scope'])):
            raise HindsightRejected('memory_target_scope_denied', 403)
        if action['send_uncertain'] or action['send_state'] in {'sending', 'unknown'}:
            payload = action['request_payload']
            desired = (fact.get('text') == payload.get('text') if action['kind'] == 'correct' else
                       fact.get('state') == ('invalidated' if action['kind'] == 'invalidate' else 'valid'))
            await asyncio.to_thread(self.store.progress, action, {**(action['verification'] or {}),
                'desired_fact_observed': desired, 'sender_quiescent': False})
            raise HindsightContractError('memory_sender_quiescence_unknown')
        evidence = action['verification']
        if evidence is None:
            evidence = await self.remote.maintenance_evidence(account, action['document_id'], None if action['affected_scopes'] == 'account' else action['scope'], target)
            await asyncio.to_thread(self.store.progress, action, evidence)
        p = action['request_payload']
        kind = action['kind']
        state = 'invalidated' if kind == 'invalidate' else 'valid' if kind == 'restore' else None
        if (kind == 'correct' and fact.get('text') != p['text']) or (state and fact.get('state') != state):
            await self.write(action, self.remote.patch_fact, account, target, text=p.get('text'), state=state, reason=p['reason'])
        current = as_dict(await self.remote.get_fact(account, target))
        current = current.get('memory', current)
        if (current.get('id') != target or current.get('document_id') != action['document_id']
                or set(current.get('tags') or []) != set(action['scope'])):
            raise HindsightContractError('memory_target_changed')
        if kind == 'correct' and current.get('text') != p['text']:
            raise HindsightContractError('memory_correction_unverified')
        if state and current.get('state') != state:
            raise HindsightContractError('memory_state_unverified')
        await self.remote.verify_fact_maintenance(account, target, action['scope'], kind, evidence)
        return {'memory_id': target, 'kind': kind, 'scope': action['scope']}
