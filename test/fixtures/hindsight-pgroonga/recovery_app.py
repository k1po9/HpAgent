"""P4 synthetic sources through real application commands; no business defaults."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT / 'test')]


async def main(args):
    from web_persistence.test_memory_governance import command
    from web_persistence.test_memory_retention import tick
    from web_persistence.test_phase_a_invariants import _conversation_and_run

    from application.memory_governance import MemoryGovernanceService
    from application.memory_retention import MemoryRetentionService
    from conversation_domain.commands import CommandService
    from memory.hindsight_client import HindsightClient, HindsightRejected
    from persistence.memory import MemoryStore
    from web_domain.outbox import OutboxService

    assert os.environ['HINDSIGHT_CONTRACT_ISOLATED'] == '1'
    api = os.environ['HINDSIGHT_CONTRACT_URL']
    assert api.startswith(('http://hpagent-pgroonga-', 'http://native-api:', 'http://candidate-api:'))
    app, worker, owner = [os.environ[k] for k in ['APP_DATABASE_URL', 'WORKER_DATABASE_URL', 'MIGRATION_DATABASE_URL']]
    assert all('hpagent_pgroonga_test' in u and 'pgroonga-' in u for u in [app, worker, owner])
    remote = HindsightClient(api)
    retention = MemoryRetentionService(worker, remote)
    outbox = OutboxService(worker)
    conn = psycopg.connect(owner, autocommit=True, options='-c search_path=hpagent,public')

    async def settle(aid):
        for _ in range(150):
            await tick(conn, outbox, retention, 'p4-recovery')
            row = conn.execute('SELECT status,error_code FROM memory_actions WHERE action_id=%s', (aid,)).fetchone()
            if row[0] == 'verified':
                return
            assert row[0] not in {'failed', 'cancelled'}, 'governance_action_failed'
            await asyncio.sleep(.2)
        raise AssertionError('governance_action_not_settled')

    async def new_source(account, text):
        _, _, run = _conversation_and_run(app, account)
        conn.execute("UPDATE messages SET content=%s WHERE message_id=(SELECT trigger_message_id FROM runs WHERE run_id=%s)", (text, run))
        commands = CommandService(worker)
        commands.start_run(account, run)
        commands.complete_run(account, run, 'synthetic preferences recorded')
        await tick(conn, outbox, retention, 'p4-recovery')
        source, doc, aid, op = conn.execute('SELECT s.source_id,s.document_id,a.action_id,a.operation_id FROM memory_sources s JOIN memory_actions a ON a.action_id=s.current_action_id WHERE s.origin_run_id=%s', (run,)).fetchone()
        await settle(aid)
        facts = await remote.list_facts(str(account), document_id=doc, state='all')
        fact = next(f['id'] for f in facts if f['fact_type'] in {'world', 'experience'})
        return dict(source=str(source), document=doc, action=str(aid), operation=str(op), fact=fact, run=str(run))

    report = {}
    try:
        if args.action == 'snapshot':
            import hashlib
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='hpagent' ORDER BY tablename").fetchall()
            result = {}
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                for (table,) in tables:
                    rows = conn.execute(f'SELECT to_jsonb(t)::text FROM hpagent."{table}" t').fetchall()
                    result[table] = dict(count=len(rows), sha256=hashlib.sha256('\n'.join(sorted(r[0] for r in rows)).encode()).hexdigest())
            print(json.dumps(dict(tables=result, passed=True)))
            return
        if args.action == 'prepare':
            account = uuid4()
            conn.execute('INSERT INTO accounts(account_id) VALUES(%s)', (account,))
            state = dict(account=str(account), bank='hpagent-u-' + str(account), arm=args.arm, sources=[])
            for text in ['Member0 prefers violet notebooks and morning tea.', 'Member1 prefers blue notebooks and evening tea.']:
                state['sources'].append(await new_source(account, text))
            first, second = state['sources']
            # Positive derived control before any curation/invalidation.
            for _ in range(150):
                evidence = await remote.maintenance_evidence(str(account), first['document'], ['scope:private'])
                if evidence['observation_ids']:
                    break
                await asyncio.sleep(.2)
            assert evidence['observation_ids']
            state['observation_ids'] = evidence['observation_ids']
            curated = command(app, account, UUID(first['source']), 'correct', first['fact'], text='Member0 prefers green notebooks and morning tea.')
            await settle(curated.body['action_id'])
            invalid = command(app, account, UUID(second['source']), 'invalidate', second['fact'])
            await settle(invalid.body['action_id'])
            assert (await remote.get_fact(str(account), second['fact']))['state'] == 'invalidated'
            assert conn.execute('SELECT manual_curation FROM memory_sources WHERE source_id=%s', (first['source'],)).fetchone()[0]
            state['cutoff'] = 'baseline_quiescent'
            Path(args.state).write_text(json.dumps(state, indent=2))
        elif args.action == 'mutate':
            state = json.loads(Path(args.state).read_text())
            account = UUID(state['account'])
            first, second = state['sources']
            result = command(app, account, UUID(first['source']), 'correct', first['fact'], text='Member0 prefers amber notebooks after cutover.')
            await settle(result.body['action_id'])
            deleted = command(app, account, UUID(second['source']), 'delete')
            await settle(deleted.body['action_id'])
            state['new_source'] = await new_source(account, 'Member2 prefers silver notebooks after cutover.')
            # A real source permission withdrawal is one transaction with epoch/generation.
            from persistence.memory_context import record_revocation
            from persistence.uow import UnitOfWork
            with UnitOfWork(worker) as uow:
                source = uow.execute('SELECT * FROM memory_sources WHERE source_id=%s FOR UPDATE', (first['source'],)).fetchone()
                record_revocation(uow, account, source, 'p4-synthetic-permission-withdrawal', [source['scope']])
            assert not MemoryStore(worker).readable_documents(account, ['scope:private'], {first['document'], second['document']})
            # A tombstone cannot be undone by restore or automatic re-registration.
            try:
                command(app, account, UUID(second['source']), 'restore', second['fact'])
                raise AssertionError('forgotten_source_restored')
            except Exception as exc:
                assert type(exc).__name__ in {'ConversationBusy', 'ResourceNotFound', 'VersionConflict'}
            state['cutoff'] = 'postwrite_quiescent'
            Path(args.state).write_text(json.dumps(state, indent=2))
        elif args.action == 'context':
            from agent_activities.store import AgentDataStore
            from application.context_assembly import ContextAssemblyService
            from application.context_builder import HarnessContextBuilder
            from run_domain.execution_context import fence_scope
            state = json.loads(Path(args.state).read_text())
            account = UUID(state['account'])
            assert state['cutoff'] == 'postwrite_quiescent'
            from hindsight_client import Hindsight
            from hindsight_client_api.models.recall_request import RecallRequest
            sdk = Hindsight(api, max_attempts=1)
            try:
                trace = (await sdk.memory.recall_memories(state['bank'], RecallRequest(
                    query='amber notebooks p4needle',trace=True,budget='mid',max_tokens=4096,
                    tags=['scope:private'],tags_match='all_strict',
                    include={'source_facts': {'max_tokens': 4096}}))).to_dict()
                keyword_ids = {r['node_id'] for arm in trace['trace']['retrieval_results']
                               if arm['method_name']=='bm25' for r in arm['results']}
                assert state['sources'][0]['fact'] in keyword_ids, 'revoked_remote_keyword_positive_missing'
                assert state['sources'][1]['fact'] not in keyword_ids, 'deleted_keyword_fact_reappeared'
                report.update(keyword_revoked_candidate_present=True,keyword_deleted_fact_absent=True)
            finally:
                await sdk.aclose()
            _, _, run = _conversation_and_run(app, account)
            commands = CommandService(worker)
            commands.start_run(account, run)
            execution = conn.execute("SELECT execution_id FROM run_executions WHERE run_id=%s AND role='root'", (run,)).fetchone()[0]
            lease = AgentDataStore(worker).acquire_lease(str(account), str(run), str(execution))
            assembly = ContextAssemblyService(worker, HarnessContextBuilder(), remote)
            with fence_scope(str(account), str(run), lease.fencing_token, str(execution)):
                base = assembly.load_base(account, run)
                items = await assembly.recall_long_term(base, 'notebooks p4needle')
            assert items, 'positive_readable_control_missing'
            denied = {d['document'] for d in state['sources']}
            for item in items:
                documents = {f['document_id'] for f in item.source_facts} if item.memory_type == 'observation' else {item.source_session_id}
                assert documents and not documents & denied, 'revoked_source_leaked'
            report.update(final_readable_selected=len(items),final_revoked_sources_absent=True)
            commands.fail_run(account, run, 'synthetic_recovery_check_finished')
        else:
            state = json.loads(Path(args.state).read_text())
            account = UUID(state['account'])
            first, second = state['sources']
            if state['cutoff'] == 'baseline_quiescent':
                assert (await remote.get_fact(str(account), second['fact']))['state'] == 'invalidated'
                assert (await remote.get_fact(str(account), first['fact']))['text'] == 'Member0 prefers green notebooks and morning tea.'
            else:
                assert (await remote.get_fact(str(account), first['fact']))['text'] == 'Member0 prefers amber notebooks after cutover.'
                try:
                    await remote.get_document(str(account), second['document'])
                    raise AssertionError('forgotten_document_reappeared')
                except HindsightRejected as exc:
                    assert exc.status == 404
                assert not MemoryStore(worker).readable_documents(account, ['scope:private'], {first['document'], second['document']})
                assert MemoryStore(worker).readable_documents(account, ['scope:private'], {state['new_source']['document']}) == {state['new_source']['document']}
                source = MemoryGovernanceService(app).source(account, UUID(second['source']))['source']
                assert source['state'] == 'forgotten'
            assert conn.execute('SELECT count(*) FROM memory_actions WHERE account_id=%s AND (status IN (\'queued\',\'uncertain\') OR send_state IN (\'sending\',\'unknown\'))', (account,)).fetchone()[0] == 0
        # Fixed action/payload identity is hashed by DB snapshot; never print bodies.
        print(json.dumps(dict(action=args.action, arm=args.arm, passed=True, account=state['account'], cutoff=state['cutoff'], **report)))
    finally:
        conn.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'mutate', 'verify', 'snapshot', 'context'])
    parser.add_argument('--arm', required=True, choices=['native', 'candidate'])
    parser.add_argument('--state', required=True)
    logging.disable(logging.CRITICAL)
    asyncio.run(main(parser.parse_args()))
