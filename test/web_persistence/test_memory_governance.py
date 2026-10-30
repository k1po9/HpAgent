"""G3 source governance through real PG commands and worker failure boundaries."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import psycopg
import pytest

from application.memory_governance import MemoryGovernanceService, as_dict
from memory.hindsight_client import HindsightRejected, HindsightUncertain
from persistence.memory import MemoryStore
from persistence.memory_context import (
    MemoryContextRevoked,
    bind_context,
    check_execution,
    check_manifest,
)
from persistence.uow import UnitOfWork
from web_domain.errors import (
    ConversationBusy,
    IdempotencyConflict,
    ResourceNotFound,
    VersionConflict,
)

from .test_memory_retention import Remote, setup_action, tick
from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


class GovernanceRemote(Remote):
    def __init__(self):
        super().__init__()
        self.fact_id = str(uuid4())
        self.facts = {}
        self.deleted = set()
        self.cleaned = []
        self.patch_calls = []
        self.document_scopes = {}
        self.fail_delete = False
        self.fail_cleanup = False

    async def retain_document(self, **payload):
        result = await super().retain_document(**payload)
        self.deleted.discard(payload['document_id'])
        self.facts[self.fact_id] = {'id': self.fact_id, 'document_id': payload['document_id'],
            'tags': ['scope:private'] if payload['scope'] == 'private' else ['scope:group', 'group:' + payload['group_id']], 'fact_type': 'world', 'text': 'old', 'state': 'valid'}
        return result

    async def get_document(self, account, document):
        if document in self.deleted or not any(o['payload']['document_id'] == document for o in self.operations.values()):
            raise HindsightRejected('hindsight_http_error', 404)
        result = await super().get_document(account, document)
        if document in self.document_scopes:
            result.update(tags=self.document_scopes[document], observation_scopes=[self.document_scopes[document]])
        return result

    async def list_facts(self, account, *, document_id=None, **kwargs):
        return [dict(f) for f in self.facts.values() if document_id is None or f['document_id'] == document_id]

    async def patch_document_scope(self, account, document, *, scope, group_id):
        tags = ['scope:private'] if scope == 'private' else ['scope:group', 'group:' + group_id]
        self.document_scopes[document] = tags
        for fact in self.facts.values():
            if fact['document_id'] == document:
                fact['tags'] = tags

    async def get_fact(self, account, target):
        if target not in self.facts:
            raise HindsightRejected('hindsight_http_error', 404)
        return dict(self.facts[target])

    async def patch_fact(self, account, target, **kwargs):
        self.patch_calls.append(kwargs)
        self.facts[target].update({k: v for k, v in kwargs.items() if k in {'text', 'state'} and v is not None})

    async def maintenance_evidence(self, account, document, scope, memory_id=None):
        return {'fact_ids': [f['id'] for f in self.facts.values() if f['document_id'] == document], 'observation_ids': []}

    async def verify_fact_maintenance(self, *args):
        return None

    async def verify_source_absent(self, account, document, scope, evidence):
        assert not self.facts
        assert document in self.deleted or not any(o['payload']['document_id'] == document for o in self.operations.values())

    async def delete_document(self, account, document):
        self.deleted.add(document)
        self.facts.clear()
        if self.fail_delete:
            raise HindsightUncertain('lost_delete_ack')

    async def delete_operation(self, account, operation):
        self.operations.pop(operation, None)
        self.cleaned.append(operation)
        if self.fail_cleanup:
            self.fail_cleanup = False
            raise HindsightUncertain('lost_cleanup_ack')


async def verified(db, database_url, worker_database_url, account_id):
    service, outbox, remote, run = await setup_action(db, database_url, worker_database_url, account_id, GovernanceRemote())
    await tick(db, outbox, service)
    for op in remote.operations.values():
        op['status'] = 'completed'
    await tick(db, outbox, service)
    source = db.execute('SELECT source_id FROM memory_sources').fetchone()[0]
    return service, outbox, remote, source, run


def command(database_url, account, source, kind, target=None, generation=None, key=None, text=None):
    svc = MemoryGovernanceService(database_url)
    if generation is None:
        generation = svc.source(account, source)['source']['generation']
    payload = {'kind': kind, 'generation': generation, 'reason': 'test'}
    if target:
        payload['memory_id'] = target
    if text:
        payload['text'] = text
    return svc.command(account, source, key or str(uuid4()), payload)


def current(db, aid):
    return db.execute('SELECT status,error_code,verification FROM memory_actions WHERE action_id=%s', (aid,)).fetchone()


async def test_correct_invalidate_restore_and_replay(db, database_url, worker_database_url, account_id):
    service, outbox, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    key = str(uuid4())
    first = command(database_url, account_id, source, 'correct', remote.fact_id, key=key, text='corrected')
    assert command(database_url, account_id, source, 'correct', remote.fact_id, key=key, generation=1, text='corrected').replayed
    with pytest.raises(IdempotencyConflict):
        command(database_url, account_id, source, 'correct', remote.fact_id, key=key, generation=1, text='different')
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 1
    assert not service.store.readable_documents(account_id, ['scope:private'], {remote.calls[0]['document_id']})
    await tick(db, outbox, service)
    assert current(db, first.body['action_id'])[0] == 'verified'
    assert remote.facts[remote.fact_id]['text'] == 'corrected'
    assert db.execute('SELECT manual_curation,state FROM memory_sources').fetchone() == (True, 'active')
    assert service.store.readable_documents(account_id, ['scope:private'], {remote.calls[0]['document_id']})
    inv = command(database_url, account_id, source, 'invalidate', remote.fact_id)
    await tick(db, outbox, service)
    assert current(db, inv.body['action_id'])[0] == 'verified'
    assert remote.facts[remote.fact_id]['state'] == 'invalidated'
    restore = command(database_url, account_id, source, 'restore', remote.fact_id)
    await tick(db, outbox, service)
    assert current(db, restore.body['action_id'])[0] == 'verified'
    assert remote.facts[remote.fact_id]['state'] == 'valid'
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 2
    with pytest.raises(ConversationBusy):
        command(database_url, account_id, source, 'restore', remote.fact_id)
    with pytest.raises(VersionConflict):
        command(database_url, account_id, source, 'invalidate', remote.fact_id, generation=1)
    # Automatic source-event replay may not undo manual curation.
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    await tick(db, outbox, service)
    assert len(remote.calls) == 1 and remote.facts[remote.fact_id]['text'] == 'corrected'


async def test_owner_and_target_scope_rejected(db, database_url, worker_database_url, account_id):
    service, outbox, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    with pytest.raises(ResourceNotFound):
        command(database_url, uuid4(), source, 'delete', generation=1)
    remote.facts[remote.fact_id]['tags'] = ['scope:group', 'group:B']
    result = command(database_url, account_id, source, 'correct', remote.fact_id, text='must not patch')
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[:2] == ('failed', 'memory_target_scope_denied')
    assert not remote.patch_calls
    assert db.execute('SELECT state FROM memory_sources').fetchone()[0] == 'blocked'


async def test_delete_tombstone_cleanup_response_loss_replay(db, database_url, worker_database_url, account_id):
    service, outbox, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    result = command(database_url, account_id, source, 'delete')
    assert db.execute('SELECT state FROM memory_sources').fetchone()[0] == 'forgotten'
    remote.fail_delete = True
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[0] == 'uncertain' and remote.operations
    remote.fail_delete = False
    remote.fail_cleanup = True
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[0] == 'uncertain'
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[0] == 'verified'
    assert not remote.operations and not remote.facts
    assert db.execute('SELECT count(*) FROM memory_actions WHERE request_payload IS NOT NULL').fetchone()[0] == 0
    with pytest.raises(ConversationBusy):
        command(database_url, account_id, source, 'restore', remote.fact_id)
    # Replayed terminal source event cannot resurrect the deleted Document.
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    await tick(db, outbox, service)
    assert len(remote.calls) == 1 and not remote.facts


async def test_never_sent_delete_and_unknown_sender_block(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id, GovernanceRemote())
    source = db.execute('SELECT source_id FROM memory_sources').fetchone()[0]
    # No wire send has occurred; deletion prevents a queued sender from sending.
    result = command(database_url, account_id, source, 'delete')
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[0] == 'verified' and not remote.calls


async def test_lost_retain_ack_never_removes_dedup_or_claims_delete(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id, GovernanceRemote())
    remote.lose_ack = True
    await tick(db, outbox, service)
    source = db.execute('SELECT source_id FROM memory_sources').fetchone()[0]
    for op in remote.operations.values():
        op['status'] = 'completed'
    result = command(database_url, account_id, source, 'delete')
    for _ in range(3):
        await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[:2] == ('uncertain', 'memory_sender_quiescence_unknown')
    assert remote.operations and not remote.cleaned
    assert not service.store.readable_documents(account_id, ['scope:private'], {remote.calls[0]['document_id']})


async def test_live_sender_excludes_delete_cleanup(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id, GovernanceRemote())
    remote.release = asyncio.Event()
    from .test_memory_retention import claim, ready
    ready(db)
    event = claim(outbox)[0]
    task = asyncio.create_task(service.process_event(event, 'g2'))
    await remote.entered.wait()
    source = db.execute('SELECT source_id FROM memory_sources').fetchone()[0]
    result = command(database_url, account_id, source, 'delete')
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[:2] == ('uncertain', 'memory_sender_active')
    assert not remote.cleaned
    remote.release.set()
    await task
    for op in remote.operations.values():
        op['status'] = 'completed'
    await tick(db, outbox, service)
    assert current(db, result.body['action_id'])[0] == 'verified'


async def test_scoped_context_snapshot_dispatch_final_history(db, database_url, worker_database_url, account_id):
    service, outbox, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    _, _, run = _conversation_and_run(database_url, account_id)
    execution = db.execute("SELECT execution_id FROM run_executions WHERE run_id=%s AND role='root'", (run,)).fetchone()[0]
    with UnitOfWork(worker_database_url) as uow:
        bind_context(uow, account_id, execution, [['scope:group', 'group:B']], {'other'}, 0)
    command(database_url, account_id, source, 'invalidate', remote.fact_id)
    with UnitOfWork(worker_database_url) as uow:
        assert check_execution(uow, account_id, run)['epoch'] == 1
        assert check_manifest(uow, account_id, {'epoch': 0, 'scopes': [], 'documents': []})['epoch'] == 1
        with pytest.raises(MemoryContextRevoked):
            check_manifest(uow, account_id, {'epoch': 0, 'scopes': [['scope:private']], 'documents': []})
        with pytest.raises(MemoryContextRevoked):
            check_manifest(uow, account_id, None)
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 1
    await tick(db, outbox, service)
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 1


async def test_real_hindsight_governance_with_pg(db, database_url, worker_database_url, account_id):
    import os

    from application.memory_retention import MemoryRetentionService
    from conversation_domain.commands import CommandService
    from memory.hindsight_client import HindsightClient
    from web_domain.outbox import OutboxService

    url = os.environ.get('HINDSIGHT_CONTRACT_URL')
    if os.environ.get('HINDSIGHT_CONTRACT_ISOLATED') != '1' or not url:
        pytest.skip('requires explicit dedicated target Hindsight')
    _, _, run = _conversation_and_run(database_url, account_id)
    db.execute("UPDATE messages SET content='Member0 prefers violet notebooks and morning tea.' WHERE role='user'")
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, run)
    worker.complete_run(account_id, run, 'User has stated their preferences.')
    remote = HindsightClient(url)
    service = MemoryRetentionService(worker_database_url, remote)
    outbox = OutboxService(worker_database_url)
    await tick(db, outbox, service)
    source, document = db.execute('SELECT source_id,document_id FROM memory_sources').fetchone()

    async def wait_action(aid):
        for _ in range(100):
            await tick(db, outbox, service)
            state = current(db, aid)
            if state[0] in {'verified', 'failed'}:
                assert state[0] == 'verified', state
                return
            await asyncio.sleep(.2)
        pytest.fail(str(current(db, aid)))

    initial = db.execute('SELECT action_id FROM memory_actions').fetchone()[0]
    await wait_action(initial)
    facts = await remote.list_facts(str(account_id), document_id=document, state='all')
    assert facts
    target = next(f['id'] for f in facts if f['fact_type'] in {'world', 'experience'})
    # Wait for a positive derived-knowledge control before maintenance.
    for _ in range(100):
        evidence = await remote.maintenance_evidence(str(account_id), document, ['scope:private'])
        if evidence['observation_ids']:
            break
        await asyncio.sleep(.2)
    assert evidence['observation_ids']
    correction = command(database_url, account_id, source, 'correct', target, text='Member0 now prefers green notebooks.')
    await wait_action(correction.body['action_id'])
    assert (await remote.get_fact(str(account_id), target))['text'] == 'Member0 now prefers green notebooks.'
    invalidation = command(database_url, account_id, source, 'invalidate', target)
    await wait_action(invalidation.body['action_id'])
    assert (await remote.get_fact(str(account_id), target))['state'] == 'invalidated'
    restoration = command(database_url, account_id, source, 'restore', target)
    await wait_action(restoration.body['action_id'])
    assert (await remote.get_fact(str(account_id), target))['state'] == 'valid'
    db.execute("UPDATE messages SET origin='{\"channel_type\":\"qq\",\"scope\":\"group\",\"context_key\":\"A\"}' WHERE message_id=(SELECT trigger_message_id FROM runs WHERE run_id=%s)", (run,))
    moved = MemoryGovernanceService(database_url).reconcile_scope(account_id, source,
        4)
    await wait_action(moved)
    assert set(as_dict(await remote.get_document(str(account_id), document))['tags']) == {'scope:group', 'group:A'}
    # Keep a positive archived-fact control through Document deletion.
    archived = command(database_url, account_id, source, 'invalidate', target)
    await wait_action(archived.body['action_id'])
    assert (await remote.get_fact(str(account_id), target))['state'] == 'invalidated'
    # A registered promotion is a separate source/Operation and is included in deletion.
    promotion = uuid4()
    promoted_doc = 'memory-promotion:' + str(promotion)
    with UnitOfWork(worker_database_url) as uow:
        payload = dict(uow.execute('SELECT request_payload FROM memory_actions WHERE action_id=%s', (initial,)).fetchone()['request_payload'])
        payload.update(document_id=promoted_doc, scope='group', group_id='B')
        uow.execute('INSERT INTO memory_sources(source_id,account_id,source_kind,source_key,origin_run_id,'
            'parent_source_id,document_id,scope,source_hash,policy_version,state) '
            "VALUES (%s,%s,'promotion',%s,%s,%s,%s,%s,%s,'chat-v2','active')",
            (promotion, account_id, promoted_doc, run, source, promoted_doc, ['scope:group', 'group:B'], '0' * 64))
        promoted = uow.execute('SELECT * FROM memory_sources WHERE source_id=%s', (promotion,)).fetchone()
        conversation = uow.execute('SELECT conversation_id FROM runs WHERE run_id=%s', (run,)).fetchone()['conversation_id']
        retained = service.store.freeze(uow, promoted, payload, 'promotion:' + str(promotion), {'conversation_id': conversation})
    await wait_action(retained['action_id'])
    assert MemoryGovernanceService(database_url).source(account_id, source)['registered_promotions'][0]['source_id'] == str(promotion)
    deletion = command(database_url, account_id, source, 'delete')
    assert set(deletion.body['source_ids']) == {str(source), str(promotion)}
    await wait_action(deletion.body['action_id'])
    assert as_dict(await remote.get_operation(str(account_id), str(retained['operation_id'])))['status'] == 'not_found'
    with pytest.raises(HindsightRejected):
        await remote.get_document(str(account_id), promoted_doc)
    with pytest.raises(HindsightRejected) as missing:
        await remote.get_document(str(account_id), document)
    assert missing.value.status == 404
    assert (await remote.get_operation(str(account_id), str(db.execute('SELECT operation_id FROM memory_actions WHERE action_id=%s', (initial,)).fetchone()[0])))['status'] == 'not_found'
    assert db.execute('SELECT count(*) FROM memory_actions WHERE request_payload IS NOT NULL').fetchone()[0] == 0


async def test_actual_snapshot_dispatch_late_tool_and_final_are_rejected(db, database_url, worker_database_url, account_id):
    from agent_activities.store import AgentDataStore
    from conversation_domain.commands import CommandService
    from resources.model_input_snapshot import SnapshotRepository
    from run_domain.execution import assert_execution_fence
    from run_domain.execution_context import fence_scope

    from .test_model_input_snapshot import _prepared, _reserve_snapshot

    _, _, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    _, _, run = _conversation_and_run(database_url, account_id)
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, run)
    execution = db.execute("SELECT execution_id FROM run_executions WHERE run_id=%s AND role='root'", (run,)).fetchone()[0]
    with UnitOfWork(worker_database_url) as uow:
        bind_context(uow, account_id, execution, [['scope:private']], {'source'}, 0)
    from application.context_assembly import ContextAssemblyService
    from application.context_builder import HarnessContextBuilder
    assembly = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    # A bootstrap retry cannot erase a manifest already bound by an earlier recall.
    stale = __import__('dataclasses').replace(assembly.load_base(account_id, run), memory_context={'epoch': 0, 'scopes': [], 'documents': []})
    assembly.bind_base(stale)
    with UnitOfWork(worker_database_url) as uow:
        assert check_execution(uow, account_id, run)['scopes'] == [['scope:private']]
    snapshot = SnapshotRepository(worker_database_url)
    values = dict(account_id=account_id, run_id=run, model_call_id=uuid4(), operation_id='g3-snapshot',
                  execution_attempt=1, call_ordinal=1, fallback_attempt=1, phase='decision', entitlement_version=1)
    ref = snapshot.create(**values, prepared=_prepared())
    _reserve_snapshot(db, account_id, run, worker_database_url, snapshot, ref)
    snapshot.mark_dispatched(account_id, ref.snapshot_id)
    store = AgentDataStore(worker_database_url)
    lease = store.acquire_lease(str(account_id), str(run), str(execution))
    command(database_url, account_id, source, 'correct', remote.fact_id, text='withdraw old context')
    with pytest.raises(MemoryContextRevoked):
        snapshot.create(**{**values, 'operation_id': 'fallback', 'fallback_attempt': 2}, prepared=_prepared())
    with pytest.raises(MemoryContextRevoked):
        snapshot.mark_dispatched(account_id, ref.snapshot_id)
    with fence_scope(str(account_id), str(run), lease.fencing_token, str(execution)):
        with pytest.raises(MemoryContextRevoked):
            store.begin_tool_operation('late-tool', str(run))
        with UnitOfWork(worker_database_url) as uow:
            with pytest.raises(MemoryContextRevoked):
                assert_execution_fence(uow)
    with pytest.raises(MemoryContextRevoked):
        worker.complete_run(account_id, run, 'late final')
    assert db.execute('SELECT content FROM messages WHERE produced_by_run_id=%s', (run,)).fetchone()[0] is None
    assert db.execute("SELECT count(*) FROM execution_operations WHERE operation_id='late-tool'").fetchone()[0] == 0


async def test_history_reuse_filters_revoked_assistant_and_rejects_stale_base(db, database_url, worker_database_url, account_id):
    from application.context_assembly import ContextAssemblyService
    from application.context_builder import HarnessContextBuilder
    from conversation_domain.commands import CommandService

    _, _, remote, source, old_run = await verified(db, database_url, worker_database_url, account_id)
    old_execution = db.execute("SELECT execution_id FROM run_executions WHERE run_id=%s", (old_run,)).fetchone()[0]
    with UnitOfWork(worker_database_url) as uow:
        bind_context(uow, account_id, old_execution, [['scope:private']], {'old'}, 0)
    conv = db.execute('SELECT conversation_id FROM runs WHERE run_id=%s', (old_run,)).fetchone()[0]
    service = CommandService(database_url)
    new_run = service.send_message(account_id, conv, str(uuid4()), 'next')['run_id']
    assembly = ContextAssemblyService(worker_database_url, HarnessContextBuilder())
    before = assembly.load_base(account_id, new_run)
    assert any(event.content.get('text') == 'answer' for event in before.short_term_events)
    command(database_url, account_id, source, 'invalidate', remote.fact_id)
    with pytest.raises(MemoryContextRevoked):
        assembly.bind_base(before)
    base = assembly.load_base(account_id, new_run)
    assembly.bind_base(base)
    assert not any(event.content.get('text') == 'answer' for event in base.short_term_events)
    execution = base.execution_id
    with UnitOfWork(worker_database_url) as uow:
        assert check_execution(uow, account_id, new_run)['scopes'] == []
        bind_context(uow, account_id, execution, [['scope:group', 'group:B']], {'B'}, 1)


@pytest.mark.parametrize('phase', ['delete', 'retain', 'never_sent'])
async def test_target_hindsight_delete_handles_never_sent_and_process_crash(
    db, database_url, worker_database_url, account_id, phase,
):
    import os
    import sys

    from memory.hindsight_client import HindsightClient

    url = os.environ.get('HINDSIGHT_CONTRACT_URL')
    if os.environ.get('HINDSIGHT_CONTRACT_ISOLATED') != '1' or not url:
        pytest.skip('requires explicit dedicated target Hindsight')
    remote = HindsightClient(url)
    service, outbox, _, _ = await setup_action(db, database_url, worker_database_url, account_id, remote)
    source, document = db.execute('SELECT source_id,document_id FROM memory_sources').fetchone()
    initial, operation = db.execute('SELECT action_id,operation_id FROM memory_actions').fetchone()
    if phase == 'never_sent':
        deletion = command(database_url, account_id, source, 'delete')
        await tick(db, outbox, service)
        assert current(db, deletion.body['action_id'])[0] == 'verified'
        assert (await remote.get_operation(str(account_id), str(operation)))['status'] == 'not_found'
        return
    if phase == 'delete':
        for _ in range(100):
            await tick(db, outbox, service)
            if current(db, initial)[0] == 'verified':
                break
            await asyncio.sleep(.2)
        assert current(db, initial)[0] == 'verified'
        deletion = command(database_url, account_id, source, 'delete')
    script = '''
import asyncio, sys
from application.memory_retention import MemoryRetentionService
from memory.hindsight_client import HindsightClient
from web_domain.outbox import OutboxService
async def main():
    database,url,phase=sys.argv[1:]
    class Pausing(HindsightClient):
        async def retain_document(self, **payload):
            result=await super().retain_document(**payload)
            if phase=='retain':
                print('WIRE_FINISHED',flush=True)
                await asyncio.sleep(60)
            return result
        async def delete_document(self, account, document):
            result=await super().delete_document(account,document)
            if phase=='delete':
                print('WIRE_FINISHED',flush=True)
                await asyncio.sleep(60)
            return result
    service=MemoryRetentionService(database,Pausing(url))
    events=OutboxService(database).claim('g3-crash',{'memory_action'},10)
    for event in events:
        await service.process_event(event,'g3-crash')
asyncio.run(main())
'''
    process = await asyncio.create_subprocess_exec(sys.executable, '-c', script, worker_database_url, url, phase,
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        assert await asyncio.wait_for(process.stdout.readline(), 20) == b'WIRE_FINISHED\n'
    finally:
        if process.returncode is None:
            process.kill()
        await process.wait()
    assert process.returncode == -9
    db.execute("UPDATE outbox_events SET locked_at=now()-interval '2 minutes' WHERE locked_by='g3-crash'")
    assert outbox.recover_expired(1, {'memory_action'}) == 1
    if phase == 'retain':
        deletion = command(database_url, account_id, source, 'delete')
    for _ in range(100):
        await tick(db, outbox, service)
        status = current(db, deletion.body['action_id'])
        if phase == 'delete' and status[0] == 'verified':
            break
        if phase == 'retain' and status[1] == 'memory_sender_quiescence_unknown':
            break
        await asyncio.sleep(.2)
    if phase == 'delete':
        assert status[0] == 'verified', status
        assert (await remote.get_operation(str(account_id), str(operation)))['status'] == 'not_found'
        with pytest.raises(HindsightRejected) as absent:
            await remote.get_document(str(account_id), document)
        assert absent.value.status == 404
        assert db.execute('SELECT count(*) FROM memory_actions WHERE request_payload IS NOT NULL').fetchone()[0] == 0
    else:
        assert status[:2] == ('uncertain', 'memory_sender_quiescence_unknown')
        assert (await remote.get_operation(str(account_id), str(operation)))['status'] != 'not_found'
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    await tick(db, outbox, service)
    assert not MemoryStore(worker_database_url).readable_documents(account_id, ['scope:private'], {document})
    if phase == 'delete':
        with pytest.raises(HindsightRejected):
            await remote.get_document(str(account_id), document)


@pytest.mark.parametrize('scope', [['scope:private'], ['scope:group', 'group:A'], ['scope:group', 'group:B']])
async def test_context_invalidation_intersects_only_maintained_scope(db, database_url, worker_database_url, account_id, scope):
    from psycopg.types.json import Jsonb

    from application.memory_retention import MemoryRetentionService
    from conversation_domain.commands import CommandService
    from web_domain.outbox import OutboxService

    _, _, run = _conversation_and_run(database_url, account_id)
    if len(scope) == 2:
        origin = {'channel_type': 'qq', 'scope': 'group', 'context_key': scope[1].removeprefix('group:')}
        db.execute('UPDATE messages SET origin=%s WHERE role=\'user\'', (Jsonb(origin),))
    commands = CommandService(worker_database_url)
    commands.start_run(account_id, run)
    commands.complete_run(account_id, run, 'answer')
    remote = GovernanceRemote()
    service, outbox = MemoryRetentionService(worker_database_url, remote), OutboxService(worker_database_url)
    await tick(db, outbox, service)
    await tick(db, outbox, service)
    for _ in range(10):
        await tick(db, outbox, service)
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 0
    for op in remote.operations.values():
        op['status'] = 'completed'
    await tick(db, outbox, service)
    source = db.execute('SELECT source_id FROM memory_sources').fetchone()[0]
    command(database_url, account_id, source, 'correct', remote.fact_id, text='new fact')
    for candidate in [['scope:private'], ['scope:group', 'group:A'], ['scope:group', 'group:B']]:
        with UnitOfWork(worker_database_url) as uow:
            manifest = {'epoch': 0, 'scopes': [candidate], 'documents': []}
            if candidate == scope:
                with pytest.raises(MemoryContextRevoked):
                    check_manifest(uow, account_id, manifest)
            else:
                assert check_manifest(uow, account_id, manifest)['epoch'] == 1
    await tick(db, outbox, service)
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 1


async def test_unknown_scope_and_missing_history_fail_closed(db, database_url, worker_database_url, account_id):
    _, _, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    db.execute("UPDATE memory_sources SET policy_version='unknown-history'")
    result = command(database_url, account_id, source, 'invalidate', remote.fact_id)
    assert db.execute('SELECT affected_scopes FROM memory_actions WHERE action_id=%s', (result.body['action_id'],)).fetchone()[0] == 'account'
    with UnitOfWork(worker_database_url) as uow:
        for scope in [['scope:private'], ['scope:group', 'group:B']]:
            with pytest.raises(MemoryContextRevoked):
                check_manifest(uow, account_id, {'epoch': 0, 'scopes': [scope], 'documents': []})
        assert check_manifest(uow, account_id, {'epoch': 0, 'scopes': [], 'documents': []})['epoch'] == 1
    # Even unrelated scopes cannot be proven safe when required history was lost.
    db.execute('UPDATE memory_actions SET invalidation_epoch=NULL WHERE action_id=%s', (result.body['action_id'],))
    with UnitOfWork(worker_database_url) as uow:
        with pytest.raises(MemoryContextRevoked):
            check_manifest(uow, account_id, {'epoch': 0, 'scopes': [['scope:group', 'group:B']], 'documents': []})


async def test_governance_api_role_cannot_read_payload_or_change_scope(db, database_url, worker_database_url, account_id):
    import psycopg

    _, _, _, source, _ = await verified(db, database_url, worker_database_url, account_id)
    for sql, params in [
        ('SELECT request_payload FROM memory_actions', ()),
        ('UPDATE memory_sources SET scope=%s WHERE source_id=%s', (['scope:group', 'group:other'], source)),
        ('UPDATE memory_actions SET operation_id=%s', (uuid4(),)),
    ]:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with UnitOfWork(database_url) as uow:
                uow.execute(sql, params)


async def test_related_resource_revoke_records_only_source_scope_once(db, database_url, worker_database_url, account_id):
    from uuid import UUID

    from conversation_domain.commands import CommandService
    from workspace.catalog import WorkspaceCatalog
    from workspace.resources import ResourcePolicy

    _, _, remote, source, run = await verified(db, database_url, worker_database_url, account_id)
    conversation = db.execute('SELECT conversation_id FROM runs WHERE run_id=%s', (run,)).fetchone()[0]
    catalog = WorkspaceCatalog(database_url, CommandService(database_url))
    tree = catalog.initialize(account_id)
    directory = UUID(next(n['node_id'] for n in tree['nodes'] if n['name'] == '资料'))
    policy = ResourcePolicy(database_url)
    grants = policy.grant(account_id, 'conversation', conversation, directory, ['read_content'], True)
    policy.revoke(account_id, UUID(grants[0]))
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 1
    action = db.execute("SELECT affected_scopes FROM memory_actions WHERE reason='resource_grant_revoked'").fetchone()[0]
    assert action == [['scope:private']]
    assert db.execute('SELECT state FROM memory_sources WHERE source_id=%s', (source,)).fetchone()[0] == 'quarantined'
    assert (await MemoryGovernanceService(database_url, remote).detail(account_id, source))['facts_available'] is False
    with pytest.raises(ConversationBusy):
        command(database_url, account_id, source, 'correct', str(uuid4()), text='cannot bypass revoked grant')
    policy.revoke(account_id, UUID(grants[0]))
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == 1
    with UnitOfWork(worker_database_url) as uow:
        assert check_manifest(uow, account_id, {'epoch': 0, 'scopes': [['scope:group', 'group:B']], 'documents': []})['epoch'] == 1


def resource_grant(database_url, account_id, conversation):
    from uuid import UUID

    from conversation_domain.commands import CommandService
    from workspace.catalog import WorkspaceCatalog
    from workspace.resources import ResourcePolicy

    tree = WorkspaceCatalog(database_url, CommandService(database_url)).initialize(account_id)
    directory = UUID(next(n['node_id'] for n in tree['nodes'] if n['name'] == '资料'))
    policy = ResourcePolicy(database_url)
    grant = policy.grant(account_id, 'conversation', conversation, directory, ['read_content'], True)
    return policy, UUID(grant[0]), directory


@pytest.mark.parametrize('phase', ['before_registration', 'before_send', 'during_operation_lookup'])
async def test_resource_revoke_blocks_delayed_retain_and_regrant_does_not_revive_it(
    db, database_url, worker_database_url, account_id, phase,
):
    from application.memory_retention import MemoryRetentionService
    from conversation_domain.commands import CommandService
    from web_domain.errors import OutboxLeaseLost
    from web_domain.outbox import OutboxService

    _, conversation, run = _conversation_and_run(database_url, account_id)
    policy, grant, directory = resource_grant(database_url, account_id, conversation)
    worker = CommandService(worker_database_url)
    assert worker.start_run(account_id, run)
    assert worker.complete_run(account_id, run, 'answer')

    class RevokeDuringLookup(GovernanceRemote):
        async def get_operation(self, *args):
            if phase == 'during_operation_lookup' and not self.revoked:
                policy.revoke(account_id, grant)
                self.revoked = True
            return await super().get_operation(*args)

    remote = RevokeDuringLookup()
    remote.revoked = False
    service = MemoryRetentionService(worker_database_url, remote)
    outbox = OutboxService(worker_database_url)
    if phase == 'before_registration':
        policy.revoke(account_id, grant)
        await tick(db, outbox, service)
    else:
        await tick(db, outbox, service)  # register, but do not submit
        if phase == 'before_send':
            policy.revoke(account_id, grant)
        else:
            # Revocation commits AFTER the consumer's initial permission read.
            # Its final send admission must still reject the stale decision.
            with pytest.raises(OutboxLeaseLost):
                await tick(db, outbox, service)
            db.execute("UPDATE outbox_events SET locked_at=now()-interval '2 minutes' "
                       "WHERE status='processing' AND event_type='memory_action'")
            assert outbox.recover_expired(1, {'memory_action'}) == 1
    for _ in range(2):
        await tick(db, outbox, service)
    document = db.execute('SELECT document_id FROM memory_sources').fetchone()[0]
    assert db.execute('SELECT memory_retention_blocked FROM runs WHERE run_id=%s', (run,)).fetchone()[0]
    with pytest.raises(psycopg.errors.RaiseException, match='immutable Run input/terminal fact'):
        db.execute('UPDATE runs SET memory_retention_blocked=false WHERE run_id=%s', (run,))
    with pytest.raises(psycopg.errors.RaiseException, match='immutable Run input/terminal fact'):
        db.execute('UPDATE runs SET created_at=clock_timestamp() WHERE run_id=%s', (run,))
    assert db.execute('SELECT state FROM memory_sources').fetchone()[0] == 'quarantined'
    assert remote.calls == []
    assert not service.store.readable_documents(account_id, ['scope:private'], {document})
    assert db.execute('SELECT status FROM runs WHERE run_id=%s', (run,)).fetchone()[0] == 'succeeded'
    # A grant permits new Runs, never retroactively authorizes the old output.
    policy.grant(account_id, 'conversation', conversation, directory, ['read_content'], True)
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    await tick(db, outbox, service)
    assert not remote.calls
    next_run = worker.send_message(account_id, conversation, str(uuid4()), 'new authorized turn')['run_id']
    assert worker.start_run(account_id, next_run)
    assert worker.complete_run(account_id, next_run, 'new answer')
    await tick(db, outbox, service)
    await tick(db, outbox, service)
    for operation in remote.operations.values():
        operation['status'] = 'completed'
    await tick(db, outbox, service)
    assert len(remote.calls) == 1
    assert remote.calls[0]['document_id'].endswith(str(next_run))
    assert service.store.readable_documents(account_id, ['scope:private'],
                                             {remote.calls[0]['document_id']})


@pytest.mark.parametrize('lost_delete_ack', [False, True])
async def test_resource_revoke_preserves_accepted_deletion_and_cleanup(
    db, database_url, worker_database_url, account_id, lost_delete_ack,
):
    service, outbox, remote, source, run = await verified(
        db, database_url, worker_database_url, account_id)
    conversation = db.execute('SELECT conversation_id FROM runs WHERE run_id=%s', (run,)).fetchone()[0]
    policy, grant, _ = resource_grant(database_url, account_id, conversation)
    deletion = command(database_url, account_id, source, 'delete')
    before = db.execute('SELECT state,generation,current_action_id FROM memory_sources').fetchone()
    if lost_delete_ack:
        remote.fail_delete = True
        await tick(db, outbox, service)
        assert current(db, deletion.body['action_id'])[0] == 'uncertain'
        remote.fail_delete = False
    epoch = db.execute('SELECT memory_epoch FROM accounts').fetchone()[0]
    policy.revoke(account_id, grant)
    policy.revoke(account_id, grant)  # repeated command must also preserve cleanup
    assert db.execute('SELECT state,generation,current_action_id FROM memory_sources').fetchone() == before
    assert before[0] == 'forgotten'
    assert db.execute('SELECT memory_epoch FROM accounts').fetchone()[0] == epoch
    await tick(db, outbox, service)
    assert current(db, deletion.body['action_id'])[0] == 'verified'
    assert not remote.facts and not remote.operations
    assert remote.deleted and remote.cleaned
    assert db.execute('SELECT count(*) FROM memory_actions WHERE request_payload IS NOT NULL').fetchone()[0] == 0
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    await tick(db, outbox, service)
    assert len(remote.calls) == 1  # the original Retain only; no resurrection
    assert db.execute('SELECT state,generation,current_action_id FROM memory_sources').fetchone() == before


async def test_unresolved_patch_cannot_be_superseded_or_unblock_delete(db, database_url, worker_database_url, account_id):
    service, outbox, remote, source, _ = await verified(db, database_url, worker_database_url, account_id)
    first = command(database_url, account_id, source, 'correct', remote.fact_id, text='uncertain correction')
    # A process may die after submitting a PATCH but before observing its response.
    db.execute("UPDATE memory_actions SET status='uncertain',send_state='sending' WHERE action_id=%s", (first.body['action_id'],))
    remote.facts[remote.fact_id]['text'] = 'uncertain correction'
    await tick(db, outbox, service)
    assert current(db, first.body['action_id'])[:2] == ('uncertain', 'memory_sender_quiescence_unknown')
    with pytest.raises(ConversationBusy):
        command(database_url, account_id, source, 'correct', remote.fact_id, text='new correction')
    deletion = command(database_url, account_id, source, 'delete')
    await tick(db, outbox, service)
    assert current(db, deletion.body['action_id'])[:2] == ('uncertain', 'memory_predecessor_unresolved')
    assert remote.operations and not remote.cleaned and not remote.patch_calls


async def test_disabled_account_cannot_send_previously_queued_retain(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    db.execute("UPDATE accounts SET status='disabled' WHERE account_id=%s", (account_id,))
    await tick(db, outbox, service)
    assert not remote.calls
    assert db.execute('SELECT status FROM memory_actions').fetchone()[0] == 'failed'
