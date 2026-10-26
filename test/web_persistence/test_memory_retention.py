"""G2: real PostgreSQL transactions, leases, recovery and read permissions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from uuid import uuid4

import psycopg
import pytest

from application.memory_retention import MemoryRetentionService, admission
from conversation_domain.commands import CommandService
from memory.hindsight_client import (
    HindsightRejected,
    HindsightUnavailable,
    HindsightUncertain,
    MemoryItem,
    RetainReceipt,
)
from persistence.memory import MemoryStore
from persistence.uow import UnitOfWork
from web_domain.errors import OutboxLeaseLost
from web_domain.outbox import OutboxService

from .test_phase_a_invariants import _conversation_and_run

pytestmark = pytest.mark.postgres


def _completed_run(database_url, worker_database_url, account_id):
    _, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    worker = CommandService(worker_database_url)
    assert worker.start_run(account_id, run_id)
    assert worker.complete_run(account_id, run_id, 'answer')
    return conversation_id, run_id


class Remote:
    """Native remote identity survives replacement of the local service/worker."""
    def __init__(self):
        self.operations = {}
        self.calls = []
        self.retries = []
        self.error = None
        self.lose_ack = False
        self.entered = asyncio.Event()
        self.release = None

    async def get_operation(self, account, operation):
        if self.error:
            raise self.error
        return {'operation_id': operation, 'status':
                self.operations.get(operation, {}).get('status', 'not_found')}

    async def retain_document(self, **payload):
        self.calls.append(deepcopy(payload))
        op = payload['operation_id']
        if op in self.operations:
            assert self.operations[op]['payload'] == payload
        else:
            self.operations[op] = {'status': 'processing', 'payload': deepcopy(payload)}
        self.entered.set()
        if self.release is not None:
            await self.release.wait()
        if self.lose_ack:
            raise HindsightUncertain('lost_ack')
        return RetainReceipt(True, 0, op, True)

    async def retry_operation(self, account, operation):
        self.retries.append(operation)
        self.operations[operation]['status'] = 'processing'

    async def get_document(self, account, document):
        p = next(o['payload'] for o in self.operations.values()
                 if o['payload']['document_id'] == document)
        tags = ['scope:private'] if p['scope'] == 'private' else ['scope:group', 'group:' + p['group_id']]
        return {'id': document, 'bank_id': 'hpagent-u-' + account,
                'tags': tags, 'observation_scopes': [tags]}


def ready(db):
    db.execute("UPDATE outbox_events SET available_at=now() WHERE status='pending'")


def claim(outbox, worker='g2'):
    return outbox.claim(worker, {'retain_memory', 'memory_action'}, 10)


async def tick(db, outbox, service, worker='g2'):
    ready(db)
    events = claim(outbox, worker)
    for event in events:
        await service.process_event(event, worker)
    return events


def action(db):
    return db.execute('SELECT action_id,operation_id,status,failure_count,request_payload,request_hash '
                      'FROM memory_actions').fetchone()


async def setup_action(db, database_url, worker_database_url, account_id, remote=None):
    remote = remote or Remote()
    _, run_id = _completed_run(database_url, worker_database_url, account_id)
    service = MemoryRetentionService(worker_database_url, remote)
    outbox = OutboxService(worker_database_url)
    await tick(db, outbox, service)
    return service, outbox, remote, run_id


async def test_registration_atomic_replay_and_payload_conflict(db, database_url, worker_database_url, account_id):
    service, outbox, remote, run_id = await setup_action(db, database_url, worker_database_url, account_id)
    original = action(db)
    assert original[2] == 'queued' and not remote.calls
    assert original[4]['events'] == [{'role': 'user', 'content': 'hello'}, {'role': 'assistant', 'content': 'answer'}]
    assert original[4]['metadata']['assistant_role'] == 'unconfirmed_assistant_response'
    assert original[4]['iso_timestamp'] == service.load_completed_run(run_id)['source_time'].isoformat()
    # Re-delivery of the terminal event reuses source/action and frozen payload.
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    events = outbox.claim('replay', {'retain_memory'}, 10)
    await asyncio.to_thread(service.prepare, events[0], 'replay')
    assert action(db) == original
    assert db.execute('SELECT count(*) FROM memory_sources').fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM outbox_events WHERE event_type='memory_action'").fetchone()[0] == 1
    with UnitOfWork(worker_database_url) as uow:
        source = uow.execute('SELECT * FROM memory_sources FOR UPDATE').fetchone()
        bad_payload = {**original[4], 'iso_timestamp': '2099-01-01T00:00:00+00:00'}
        with pytest.raises(ValueError, match='memory_request_conflict'):
            service.store.freeze(uow, source, bad_payload, 'retain:' + str(run_id), events[0])
    assert action(db) == original
    # A changed source under the same logical key is a permanent conflict,
    # not an endlessly retried upload or a replacement of the frozen action.
    db.execute("UPDATE messages SET content='changed payload' WHERE role='user'")
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    conflict = outbox.claim('conflict', {'retain_memory'}, 1)[0]
    await service.process_event(conflict, 'conflict')
    assert db.execute("SELECT status,last_error_code FROM outbox_events WHERE event_type='retain_memory'").fetchone() == ('dead_letter', 'memory_request_conflict')
    assert action(db) == original and not remote.calls


async def test_lost_ack_restart_not_found_replay_uses_same_identity(db, database_url, worker_database_url, account_id):
    service, outbox, remote, run_id = await setup_action(db, database_url, worker_database_url, account_id)
    remote.lose_ack = True
    await tick(db, outbox, service)
    assert action(db)[2:4] == ('uncertain', 1)
    old = deepcopy(remote.calls[0])
    # 404/not_found after a possibly sent request must replay the original UUID/payload.
    remote.operations.clear()
    remote.lose_ack = False
    restarted = MemoryRetentionService(worker_database_url, remote)
    await tick(db, outbox, restarted, 'new-process')
    assert remote.calls == [old, old]
    remote.operations[old['operation_id']]['status'] = 'completed'
    await tick(db, outbox, restarted)
    assert action(db)[2] == 'verified'
    assert db.execute('SELECT status FROM runs WHERE run_id=%s', (run_id,)).fetchone()[0] == 'succeeded'


async def test_slow_operation_outlives_delivery_limit_without_failure(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    await tick(db, outbox, service)
    for _ in range(12):
        await tick(db, outbox, service)
    assert action(db)[2:4] == ('submitted', 0)
    assert len(remote.calls) == 1
    assert db.execute("SELECT attempt_count FROM outbox_events WHERE event_type='memory_action'").fetchone()[0] > 8
    remote.operations[remote.calls[0]['operation_id']]['status'] = 'completed'
    await tick(db, outbox, service)
    assert action(db)[2] == 'verified'


async def test_dual_workers_old_lease_same_worker_name_cannot_commit(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    remote.release = asyncio.Event()
    event = claim(outbox)[0]
    old = asyncio.create_task(service.process_event(event, 'g2'))
    await asyncio.wait_for(remote.entered.wait(), 5)
    operation = remote.calls[0]['operation_id']
    remote.operations[operation]['status'] = 'completed'
    db.execute("UPDATE outbox_events SET locked_at=now()-interval '2 minutes' WHERE event_type='memory_action'")
    assert outbox.recover_expired(60, {'memory_action'}) == 1
    # Same worker name simulates restarted process identity. Delivery fence differs.
    new = claim(outbox)[0]
    assert new['attempt_count'] > event['attempt_count']
    await MemoryRetentionService(worker_database_url, remote).process_event(new, 'g2')
    assert action(db)[2] == 'verified'
    remote.release.set()
    with pytest.raises(OutboxLeaseLost):
        await old
    assert action(db)[2:4] == ('verified', 0) and len(remote.calls) == 1


async def test_generation_changed_during_remote_call_cannot_verify(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    await tick(db, outbox, service)
    remote.operations[remote.calls[0]['operation_id']]['status'] = 'completed'
    db.execute("UPDATE memory_sources SET generation=generation+1,state='blocked'")
    await tick(db, outbox, service)
    assert action(db)[2] == 'uncertain'
    assert not MemoryStore(worker_database_url).readable_documents(account_id, ['scope:private'], {remote.calls[0]['document_id']})


@pytest.mark.parametrize('status', [400, 401, 403, 422])
async def test_permanent_4xx_stops_submissions(db, database_url, worker_database_url, account_id, status):
    service, outbox, remote, run_id = await setup_action(db, database_url, worker_database_url, account_id)
    async def reject(**kwargs):
        remote.calls.append(kwargs)
        raise HindsightRejected('hindsight_http_error', status)
    remote.retain_document = reject
    await tick(db, outbox, service)
    for _ in range(3):
        assert not await tick(db, outbox, service)
    assert len(remote.calls) == 1 and action(db)[2:4] == ('failed', 1)
    assert db.execute('SELECT status FROM runs WHERE run_id=%s', (run_id,)).fetchone()[0] == 'succeeded'


async def test_outage_then_recovery(db, database_url, worker_database_url, account_id):
    service, outbox, remote, run_id = await setup_action(db, database_url, worker_database_url, account_id)
    remote.error = HindsightUnavailable('hindsight_http_error', 503)
    for _ in range(10):
        await tick(db, outbox, service)
    assert action(db)[2:4] == ('queued', 10)
    assert db.execute('SELECT status FROM runs WHERE run_id=%s', (run_id,)).fetchone()[0] == 'succeeded'
    remote.error = None
    await tick(db, outbox, service)
    operation = remote.calls[0]['operation_id']
    remote.operations[operation]['status'] = 'completed'
    await tick(db, outbox, service)
    assert action(db)[2] == 'verified'


async def test_native_retry_preserves_remote_identity(db, database_url, worker_database_url, account_id):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    await tick(db, outbox, service)
    operation = remote.calls[0]['operation_id']
    remote.operations[operation]['status'] = 'failed'
    await tick(db, outbox, service)
    assert remote.retries == [operation] and len(remote.calls) == 1
    assert action(db)[2:4] == ('submitted', 1)
    remote.operations[operation]['status'] = 'completed'
    await tick(db, outbox, service)
    assert action(db)[2] == 'verified'


async def test_source_and_observation_read_gate(db, database_url, worker_database_url, account_id):
    from application.context_assembly import ContextAssemblyService
    from application.context_builder import HarnessContextBuilder
    service, outbox, remote, run_id = await setup_action(db, database_url, worker_database_url, account_id)
    document = action(db)[4]['document_id']
    store = service.store
    assert store.readable_documents(account_id, ['scope:private'], {document}) == set()
    await tick(db, outbox, service)
    remote.operations[remote.calls[0]['operation_id']]['status'] = 'completed'
    await tick(db, outbox, service)
    assert store.readable_documents(account_id, ['scope:private'], {document}) == {document}
    assert not store.readable_documents(uuid4(), ['scope:private'], {document})
    assert not store.readable_documents(account_id, ['scope:group', 'group:A'], {document})
    fact = MemoryItem('fact', source_session_id=document, tags=('scope:private',))
    old = MemoryItem('legacy', source_session_id='unknown', tags=('scope:private',))
    obs = MemoryItem('observation', memory_type='observation', tags=('scope:private',),
                     source_fact_ids=('a','b'), source_facts=({'document_id':document}, {'document_id':'unknown'}))
    verified_obs = MemoryItem('verified observation', memory_type='observation', tags=('scope:private',),
                              source_fact_ids=('a',), source_facts=({'document_id': document},))
    async def recall_candidates(*args, **kwargs): return [fact, old, obs, verified_obs]
    remote.recall_candidates = recall_candidates
    assembly = ContextAssemblyService(worker_database_url, HarnessContextBuilder(), remote)
    _, _, next_run = _conversation_and_run(database_url, account_id)
    base = assembly.load_base(account_id, next_run)
    assert await assembly.recall_long_term(base, 'test') == (fact, verified_obs)
    db.execute("UPDATE memory_sources SET state='blocked'")
    assert await assembly.recall_long_term(base, 'test') == ()


@pytest.mark.parametrize(('content','decision'), [('不要记住这个', 'reject'), ('password: abc123','reject'), ('确诊糖尿病','evidence_required')])
async def test_special_content_stays_local(db, database_url, worker_database_url, account_id, content, decision):
    _, _, run_id = _conversation_and_run(database_url, account_id)
    db.execute("UPDATE messages SET content=%s WHERE role='user'", (content,))
    worker = CommandService(worker_database_url)
    worker.start_run(account_id, run_id)
    worker.complete_run(account_id, run_id, 'answer')
    remote = Remote()
    service = MemoryRetentionService(worker_database_url, remote)
    await tick(db, OutboxService(worker_database_url), service)
    assert admission(content, 'answer') == decision
    assert db.execute('SELECT state FROM memory_sources').fetchone()[0] == ('blocked' if decision == 'reject' else 'quarantined')
    assert action(db) is None and not remote.calls


def test_schema_composite_ownership_and_roles(db, database_url, worker_database_url, account_id):
    assert db.execute('SELECT memory_epoch FROM accounts WHERE account_id=%s', (account_id,)).fetchone()[0] == 0
    with psycopg.connect(database_url) as api:
        api.execute('SET search_path TO hpagent, public')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            api.execute('SELECT request_payload FROM memory_actions')
    _, run_id = _completed_run(database_url, worker_database_url, account_id)
    other = uuid4()
    db.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        db.execute("INSERT INTO memory_sources(source_id,account_id,source_kind,source_key,origin_run_id,"
                   "document_id,scope,source_hash,policy_version,state) "
                   "VALUES (%s,%s,'chat','x',%s,'x',ARRAY['scope:private'],%s,'v2','active')",
                   (uuid4(), other, run_id, 'a'*64))


async def test_sigkill_after_remote_accept_before_ack_recovers_fixed_sdk_request(
    db, database_url, worker_database_url, account_id,
):
    """Kill a real Python worker while an HTTP fixture holds the accepted ack."""
    import os
    import sys

    from aiohttp import web

    from memory.hindsight_client import HindsightClient

    service, outbox, _, run_id = await setup_action(db, database_url, worker_database_url, account_id)
    requests = []
    accepted = asyncio.Event()
    release = asyncio.Event()
    remote_status = 'not_found'
    operation = str(action(db)[1])
    document = action(db)[4]['document_id']
    bank = 'hpagent-u-' + str(account_id)

    async def handler(request):
        nonlocal remote_status
        if request.method == 'POST':
            body = await request.json()
            assert body['operation_id'] == operation and body['async'] is True
            requests.append(body)
            remote_status = 'processing'
            accepted.set()
            await release.wait()
            return web.json_response({'success': True, 'bank_id': bank, 'items_count': 0,
                                      'async': True, 'operation_id': operation})
        if '/operations/' in request.path:
            return web.json_response({'operation_id': operation, 'status': remote_status})
        return web.json_response({'id': document, 'bank_id': bank, 'tags': ['scope:private'],
                                  'observation_scopes': [['scope:private']],
                                  'original_text': None, 'content_hash': None,
                                  'created_at': '2026-10-25T00:00:00Z',
                                  'updated_at': '2026-10-25T00:00:00Z', 'memory_unit_count': 1})

    app = web.Application()
    app.router.add_route('*', '/{tail:.*}', handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '127.0.0.1', 0)
    await site.start()
    url = 'http://127.0.0.1:' + str(site._server.sockets[0].getsockname()[1])
    script = '''
import asyncio, os
from application.memory_retention import MemoryRetentionService
from memory.hindsight_client import HindsightClient
from web_domain.outbox import OutboxService
async def main():
    db = os.environ['WORKER_DATABASE_URL']
    event = OutboxService(db).claim('crash-worker', {'memory_action'}, 1)[0]
    await MemoryRetentionService(db, HindsightClient(os.environ['G2_FAKE_REMOTE'], retain_timeout=60)).process_event(event, 'crash-worker')
asyncio.run(main())
'''
    proc = await asyncio.create_subprocess_exec(
        sys.executable, '-c', script, env={**os.environ, 'G2_FAKE_REMOTE': url,
                                          'PYTHONPATH': os.path.abspath('src')},
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(accepted.wait(), 10)
        proc.kill()
        await asyncio.wait_for(proc.wait(), 10)
        assert proc.returncode == -9
        assert action(db)[2] == 'submitted'  # dispatch intent committed before HTTP
        release.set()
        db.execute("UPDATE outbox_events SET locked_at=now()-interval '2 minutes' WHERE event_type='memory_action'")
        assert outbox.recover_expired(60, {'memory_action'}) == 1
        # Even a lost remote lookup replays exactly the same wire body/UUID.
        remote_status = 'not_found'
        restarted = MemoryRetentionService(worker_database_url, HindsightClient(url))
        await tick(db, outbox, restarted, 'recovered-process')
        assert len(requests) == 2 and requests[0] == requests[1]
        remote_status = 'completed'
        await tick(db, outbox, restarted, 'recovered-process')
        assert action(db)[2] == 'verified' and str(action(db)[1]) == operation
        assert db.execute('SELECT status FROM runs WHERE run_id=%s', (run_id,)).fetchone()[0] == 'succeeded'
    finally:
        release.set()
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        await runner.cleanup()


async def test_concurrent_duplicate_terminal_events_register_one_action(db, database_url, worker_database_url, account_id):
    _completed_run(database_url, worker_database_url, account_id)
    db.execute("INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,conversation_id,run_id,payload) "
               "SELECT %s,account_id,event_type,%s,conversation_id,run_id,payload FROM outbox_events WHERE event_type='retain_memory'",
               (uuid4(), 'duplicate-terminal-event:' + str(uuid4())))
    outbox = OutboxService(worker_database_url)
    a = outbox.claim('worker-a', {'retain_memory'}, 1)[0]
    b = outbox.claim('worker-b', {'retain_memory'}, 1)[0]
    await asyncio.gather(
        asyncio.to_thread(MemoryRetentionService(worker_database_url, Remote()).prepare, a, 'worker-a'),
        asyncio.to_thread(MemoryRetentionService(worker_database_url, Remote()).prepare, b, 'worker-b'))
    assert db.execute('SELECT count(*) FROM memory_sources').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM memory_actions').fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM outbox_events WHERE event_type='memory_action'").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM outbox_events WHERE event_type='retain_memory' AND status='processed'").fetchone()[0] == 2


async def test_registration_transaction_rolls_back_all_four_facts(db, database_url, worker_database_url, account_id, monkeypatch):
    _completed_run(database_url, worker_database_url, account_id)
    service = MemoryRetentionService(worker_database_url, Remote())
    event = OutboxService(worker_database_url).claim('fault-worker', {'retain_memory'}, 1)[0]
    enqueue = service.store.outbox.enqueue
    def fail_after_enqueue(*args, **kwargs):
        enqueue(*args, **kwargs)
        raise RuntimeError('injected_registration_failure')
    with monkeypatch.context() as patch:
        patch.setattr(service.store.outbox, 'enqueue', fail_after_enqueue)
        with pytest.raises(RuntimeError, match='injected_registration_failure'):
            await asyncio.to_thread(service.prepare, event, 'fault-worker')
    assert db.execute('SELECT count(*) FROM memory_sources').fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM memory_actions').fetchone()[0] == 0
    assert db.execute("SELECT count(*) FROM outbox_events WHERE event_type='memory_action'").fetchone()[0] == 0
    assert db.execute("SELECT status FROM outbox_events WHERE event_type='retain_memory'").fetchone()[0] == 'processing'
    await asyncio.to_thread(service.prepare, event, 'fault-worker')
    assert action(db)[2] == 'queued'


async def test_real_hindsight_pipeline_recovers_after_outage(db, database_url, worker_database_url, account_id):
    import os

    from memory.hindsight_client import HindsightClient

    url = os.environ.get('HINDSIGHT_CONTRACT_URL')
    if os.environ.get('HINDSIGHT_CONTRACT_ISOLATED') != '1' or not url:
        pytest.skip('requires explicit dedicated Hindsight contract service')
    service, outbox, _, _ = await setup_action(db, database_url, worker_database_url, account_id)
    original = action(db)
    # Real SDK with connection refused, then restore the dedicated target service.
    service._hindsight = HindsightClient('http://127.0.0.1:1', timeout=0.1)
    await tick(db, outbox, service)
    assert action(db)[2:4] == ('queued', 1)
    service._hindsight = HindsightClient(url)
    try:
        for _ in range(100):
            await tick(db, outbox, service)
            if action(db)[2] == 'verified':
                break
            await asyncio.sleep(0.2)
        assert action(db)[2] == 'verified'
        assert action(db)[:2] == original[:2] and action(db)[4:] == original[4:]
        assert service.store.readable_documents(account_id, ['scope:private'], {original[4]['document_id']}) == {original[4]['document_id']}
    finally:
        await service._hindsight.delete_document(str(account_id), original[4]['document_id'])
        await service._hindsight.delete_operation(str(account_id), str(original[1]))


@pytest.mark.parametrize('state', ['queued', 'failed', 'cancelled'])
async def test_non_successful_runs_never_register_or_upload(db, database_url, worker_database_url, account_id, state):
    _, conversation_id, run_id = _conversation_and_run(database_url, account_id)
    if state == 'failed':
        assert CommandService(worker_database_url).fail_run(account_id, run_id, 'fixture_failure')
    elif state == 'cancelled':
        CommandService(database_url).cancel_run(account_id, run_id, str(uuid4()))
    remote = Remote()
    service = MemoryRetentionService(worker_database_url, remote)
    assert service.load_completed_run(run_id) is None
    db.execute("INSERT INTO outbox_events(outbox_event_id,account_id,event_type,business_key,conversation_id,run_id,payload) "
               "VALUES (%s,%s,'retain_memory',%s,%s,%s,'{}')",
               (uuid4(), account_id, 'invalid-retain:' + str(run_id), conversation_id, run_id))
    await tick(db, OutboxService(worker_database_url), service)
    assert not remote.calls and action(db) is None
    assert db.execute("SELECT status FROM outbox_events WHERE event_type='retain_memory'").fetchone()[0] == 'processed'


@pytest.mark.parametrize('scope', [['scope:group', None], ['scope:group', 'group:'], ['scope:work:' + str(uuid4())]])
def test_source_scope_shape_constraints(db, database_url, worker_database_url, account_id, scope):
    _, run_id = _completed_run(database_url, worker_database_url, account_id)
    with pytest.raises(psycopg.errors.CheckViolation):
        db.execute("INSERT INTO memory_sources(source_id,account_id,source_kind,source_key,origin_run_id,"
                   "document_id,scope,source_hash,policy_version,state) "
                   "VALUES (%s,%s,'chat','x',%s,'x',%s,%s,'v2','active')",
                   (uuid4(), account_id, run_id, scope, 'a'*64))


async def test_quality_snapshot_survives_configuration_change_and_terminal_replay(
    db, database_url, worker_database_url, account_id
):
    remote = Remote()
    remote.quality_snapshot = lambda: {'config_hash': 'first-profile', 'bank': {'retain_chunk_size': 3000},
                                      'text_search': {'extension': 'native', 'native_language': 'english'}}
    service, outbox, _, _ = await setup_action(db, database_url, worker_database_url, account_id, remote)
    original = action(db)
    assert original[4]['quality_snapshot']['config_hash'] == 'first-profile'
    remote.quality_snapshot = lambda: {'config_hash': 'second-profile', 'bank': {'retain_chunk_size': 4000},
                                      'text_search': {'extension': 'pgroonga', 'native_language': None}}
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    events = outbox.claim('quality-replay', {'retain_memory'}, 10)
    await asyncio.to_thread(service.prepare, events[0], 'quality-replay')
    assert action(db) == original
    await tick(db, outbox, service)
    assert remote.calls[0]['quality_snapshot'] == original[4]['quality_snapshot']
    assert remote.calls[0]['operation_id'] == str(original[1])


async def test_legacy_action_does_not_acquire_new_quality_snapshot(
    db, database_url, worker_database_url, account_id
):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    original = action(db)
    remote.quality_snapshot = lambda: {'config_hash': 'new-profile'}
    db.execute("UPDATE outbox_events SET status='pending',processed_at=NULL WHERE event_type='retain_memory'")
    events = outbox.claim('legacy-quality-replay', {'retain_memory'}, 10)
    await asyncio.to_thread(service.prepare, events[0], 'legacy-quality-replay')
    assert action(db) == original
    assert 'quality_snapshot' not in action(db)[4]


async def test_verified_document_counts_are_not_retain_item_counts(
    db, database_url, worker_database_url, account_id
):
    service, outbox, remote, _ = await setup_action(db, database_url, worker_database_url, account_id)
    original_get = remote.get_document
    async def counted(account, document):
        result = await original_get(account, document)
        return {**result, 'memory_unit_count': 3, 'nodes_by_fact_type': {'world': 3}}
    remote.get_document = counted
    await tick(db, outbox, service)
    remote.operations[str(action(db)[1])]['status'] = 'completed'
    await tick(db, outbox, service)
    verification = db.execute('SELECT verification FROM memory_actions').fetchone()[0]
    assert verification['memory_unit_count'] == 3
    assert verification['nodes_by_fact_type'] == {'world': 3}


@pytest.mark.parametrize('error,expected', [
    (HindsightUnavailable('memory_bank_config_not_applied'), 'queued'),
    (HindsightUncertain('memory_bank_config_ack_unknown'), 'uncertain'),
])
async def test_bank_preflight_failure_does_not_claim_source_was_sent(
    db, database_url, worker_database_url, account_id, error, expected
):
    remote = Remote()
    remote.quality_snapshot = lambda: {'config_hash': 'profile'}
    async def prepare(account, snapshot):
        assert snapshot == {'config_hash': 'profile'}
        raise error
    remote.prepare_retain = prepare
    service, outbox, _, _ = await setup_action(db, database_url, worker_database_url, account_id, remote)
    original = action(db)
    await tick(db, outbox, service)
    assert action(db)[2] == expected
    assert db.execute('SELECT send_state,send_uncertain FROM memory_actions').fetchone() == ('idle', False)
    assert not remote.calls
    del remote.prepare_retain
    await tick(db, outbox, service)
    assert remote.calls[0]['operation_id'] == str(original[1])
    assert remote.calls[0]['quality_snapshot'] == original[4]['quality_snapshot']


@pytest.mark.parametrize('state,match,legacy,allowed', [
    ('idle', True, False, True), ('idle', False, False, False),
    ('unknown', True, False, False), ('idle', True, True, False),
])
async def test_only_matching_proven_unsent_profile_allows_configuration_repair(
    db, database_url, worker_database_url, account_id, state, match, legacy, allowed
):
    from memory.configuration import local_accounts
    from memory.quality_config import MemoryConfigError

    remote = Remote()
    if not legacy:
        remote.quality_snapshot = lambda: {'config_hash': 'profile'}
    service, _, _, _ = await setup_action(db, database_url, worker_database_url, account_id, remote)
    if state == 'unknown':
        service.store.send_state({'account_id': account_id, 'action_id': action(db)[0]}, 'unknown')
    args = dict(require_idle=True, expected_profile_hash='profile' if match else 'different')
    if allowed:
        assert local_accounts(worker_database_url, [str(account_id)], **args) == [str(account_id)]
    else:
        with pytest.raises(MemoryConfigError, match='not_drained'):
            local_accounts(worker_database_url, [str(account_id)], **args)


async def test_real_bank_profile_repair_resumes_the_same_frozen_action(
    db, database_url, worker_database_url, account_id, tmp_path
):
    import json
    import os
    import subprocess
    import sys
    import time
    from pathlib import Path

    from hindsight_client import Hindsight
    from hindsight_client_api.models.create_bank_request import CreateBankRequest

    from memory.hindsight_client import HindsightClient
    from memory.quality_config import MemoryQualityConfig, load_memory_prompts

    url = os.environ.get('HINDSIGHT_CONTRACT_URL')
    if os.environ.get('HINDSIGHT_CONTRACT_ISOLATED') != '1' or not url:
        pytest.skip('requires explicit dedicated target Hindsight')
    root = Path(__file__).resolve().parents[2]
    quality = MemoryQualityConfig.from_yaml(root / 'config/hindsight.yaml')
    prompts = load_memory_prompts(root / 'config/prompts/memory.yaml')
    sdk = Hindsight(url, max_attempts=1)
    try:
        await sdk.banks.create_or_update_bank('hpagent-u-' + str(account_id),
                                             CreateBankRequest(retain_mission='Original profile.'))
    finally:
        await sdk.aclose()
    remote = HindsightClient(url, quality_config=quality, memory_prompts=prompts)
    service, outbox, _, _ = await setup_action(db, database_url, worker_database_url, account_id, remote)
    original = action(db)
    await tick(db, outbox, service)
    assert action(db)[2] == 'queued'
    assert db.execute('SELECT send_state FROM memory_actions').fetchone()[0] == 'idle'
    assert (await remote.get_operation(str(account_id), str(original[1])))['status'] == 'not_found'
    result = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, 'scripts/operations/memory-config.py', 'apply', '--senders-stopped',
         '--account', str(account_id), '--backup', str(tmp_path / 'profile-backup.json')],
        cwd=root, env={**os.environ, 'HINDSIGHT_URL': url}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['verified_banks'] == 1
    deadline = time.monotonic() + 90
    while action(db)[2] != 'verified':
        assert time.monotonic() < deadline
        await tick(db, outbox, service)
        await asyncio.sleep(0.05)
    after = action(db)
    assert after[0:2] == original[0:2]
    assert after[4:6] == original[4:6]
