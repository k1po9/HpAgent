from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import psycopg
import pytest

from conversation_domain.commands import CommandService
from persistence.uow import UnitOfWork
from research_domain.models import ResearchPlan, ResearchQuestion, SourceStrategy
from research_domain.persistence import ResearchRepository
from research_domain.services import ResearchQueryService
from run_domain.lifecycle import RunLifecycleService
from web_domain.errors import ConversationBusy, IdempotencyConflict, ResourceNotFound
from work_domain.commands import WorkConflict
from work_domain.completion import WorkCompletionPolicy
from work_domain.models import Requirement
from work_domain.persistence import WorkRepository
from workspace.resources import ResourcePolicy

pytestmark = pytest.mark.postgres


def requirement(capability='reminder', **changes):
    return Requirement.from_dict({
        'objective': 'a durable mandate', 'capability_key': capability,
        'spec': {'schema_version':1, **({'content':'a reminder'} if capability=='reminder' else
                                      {'source_strategy': SourceStrategy().to_dict()})},
        'acceptance_criteria':[{'id':'deliver', 'required':True,
                                'evidence_types':['operation_receipt' if capability=='reminder' else 'research_report']}],
        **changes,
    })


def accept(commands, account_id, capability='reminder', **changes):
    return commands.accept(account_id, str(uuid4()), 'Mandate', requirement(capability, **changes)).body['work']


def advance(commands, account_id, work):
    result = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'])
    return result.body['work'], result.body['run']


def controlled_result(ref='controlled-test-receipt:1'):
    return {'schema_version':1, 'kind':'progress_saved',
            'evidence':[{'criterion_id':'deliver','type':'operation_receipt','ref':ref}]}


def persisted_result(database, account_id, run):
    from agent_activities.fencing import fence_scope
    from agent_activities.store import AgentDataStore

    store = AgentDataStore(database)
    lease = store.acquire_lease(str(account_id), run['run_id'])
    operation = f'{lease.execution_id}:verified-action'
    with fence_scope(str(account_id), run['run_id'], lease.fencing_token, lease.execution_id):
        store.begin_tool_operation(operation, run['run_id'])
        store.complete_operation(operation, f'operation:{operation}',
                                 {'tool_success': True, 'side_effect_class': 'read_only'})
    return controlled_result(f'operation:{operation}')


@pytest.mark.parametrize('capability', ['reminder','research_report'])
def test_accept_without_conversation_and_idempotency(commands, account_id, owner, capability):
    key = str(uuid4())
    result = commands.accept(account_id, key, 'Mandate', requirement(capability))
    replay = commands.accept(account_id, key, 'Mandate', requirement(capability))
    assert result.body == replay.body and replay.replayed
    with pytest.raises(IdempotencyConflict):
        commands.accept(account_id, key, 'Different', requirement(capability))
    work_id = UUID(result.body['work']['work_id'])
    assert owner.execute('SELECT count(*) AS n FROM work_events WHERE work_id=%s', (work_id,)).fetchone()['n']==1
    event = owner.execute('SELECT * FROM outbox_events WHERE work_id=%s', (work_id,)).fetchone()
    assert event['run_id'] is None and event['aggregate_id']==work_id


def test_cross_conversation_links_do_not_copy_permissions(commands, account_id, owner):
    conversations = [uuid4(), uuid4()]
    for cid in conversations:
        owner.execute('INSERT INTO conversations(account_id,conversation_id) VALUES (%s,%s)', (account_id,cid))
    work = accept(commands, account_id)
    for cid in conversations:
        work = commands.link(account_id, UUID(work['work_id']), cid, str(uuid4()), work['row_version']).body['work']
    assert set(work['conversation_ids'])==set(map(str,conversations))
    assert ResourcePolicy(commands.database).grants(account_id, 'work', UUID(work['work_id']))==[]


def test_account_scope_queries_and_fk(commands, account_id, owner):
    work = accept(commands, account_id)
    other = uuid4()
    owner.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
    cid = uuid4()
    owner.execute('INSERT INTO conversations(account_id,conversation_id) VALUES (%s,%s)', (other,cid))
    with pytest.raises(ResourceNotFound):
        commands.get(other, UUID(work['work_id']))
    with pytest.raises(ResourceNotFound):
        commands.link(account_id, UUID(work['work_id']), cid, str(uuid4()), work['row_version'])
    command = owner.execute('SELECT command_id FROM work_requirements WHERE work_id=%s', (work['work_id'],)).fetchone()['command_id']
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        owner.execute('INSERT INTO work_conversations(account_id,work_id,conversation_id,linked_by_command_id) '
                      'VALUES (%s,%s,%s,%s)', (account_id, work['work_id'], cid, command))


def test_requirement_is_immutable_and_revise_cas(commands, account_id, owner):
    work = accept(commands, account_id)
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('UPDATE work_requirements SET objective=\'mutated\' WHERE work_id=%s', (work['work_id'],))
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('DELETE FROM work_requirements WHERE work_id=%s', (work['work_id'],))
    updated = commands.revise(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'],
                              replace(requirement(), objective='new objective')).body['work']
    assert updated['current_requirement_revision']==2 and updated['control_epoch']==1
    with pytest.raises(WorkConflict):
        commands.revise(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'], requirement())
    rows = owner.execute('SELECT objective FROM work_requirements WHERE work_id=%s ORDER BY revision',
                         (work['work_id'],)).fetchall()
    assert [r['objective'] for r in rows]==['a durable mandate','new objective']


@pytest.mark.parametrize('action', ['revise','advance'])
def test_concurrent_commands_allow_one_winner(commands, account_id, owner, action):
    work = accept(commands, account_id)
    barrier = Barrier(2)

    def invoke():
        barrier.wait()
        try:
            if action=='advance':
                commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'])
            else:
                commands.revise(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'], requirement())
            return 'success'
        except WorkConflict:
            return 'conflict'

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke) for _ in range(2)]
        assert sorted(f.result(timeout=15) for f in futures)==['conflict','success']
    assert owner.execute('SELECT count(*) AS n FROM runs WHERE work_id=%s AND '
                         'status IN (\'queued\',\'running\',\'cancelling\')', (work['work_id'],)).fetchone()['n'] <= 1


def test_queued_coordinator_and_different_work_admission(commands, account_id, owner):
    work, run = advance(commands, account_id, accept(commands, account_id))
    assert run['status']=='queued' and work['active_coordinator_run_id']==run['run_id']
    assert run['conversation_id'] is None and run['session_id'] is None
    with pytest.raises(WorkConflict):
        commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'])
    other, other_run = advance(commands, account_id, accept(commands, account_id))
    assert other_run['run_id']!=run['run_id']
    assert owner.execute('SELECT count(*) AS n FROM messages WHERE produced_by_run_id=ANY(%s)',
                         ([UUID(run['run_id']),UUID(other_run['run_id'])],)).fetchone()['n']==0


def test_pointer_consistency_and_run_owner_immutability(commands, account_id, owner):
    work, run = advance(commands, account_id, accept(commands, account_id))
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('UPDATE works SET active_coordinator_run_id=NULL,row_version=row_version+1 '
                      'WHERE work_id=%s', (work['work_id'],))
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('UPDATE runs SET executor_version=2 WHERE run_id=%s', (run['run_id'],))
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('UPDATE runs SET requirement_revision=2 WHERE run_id=%s', (run['run_id'],))


def test_run_success_does_not_complete_work(commands, account_id, urls, owner):
    work, run = advance(commands, account_id, accept(commands, account_id))
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, UUID(run['run_id']))
    result = persisted_result(urls[2], account_id, run)
    assert lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded', result=result)
    latest = commands.get(account_id, UUID(work['work_id']))['work']
    assert latest['status']=='active' and latest['active_coordinator_run_id'] is None
    assert latest['completed_requirement_revision'] is None
    with UnitOfWork(urls[2]) as uow:
        current = WorkRepository.get(uow, account_id, UUID(work['work_id']), lock=True)
        result_run = uow.execute('SELECT * FROM runs WHERE run_id=%s', (run['run_id'],)).fetchone()
        assert WorkCompletionPolicy.accept(uow,current,dict(result_run))
    completed = commands.get(account_id, UUID(work['work_id']))['work']
    assert completed['status']=='completed' and completed['completed_requirement_revision']==1
    assert completed['completion_receipt']['evidence']==result['evidence']
    with pytest.raises(WorkConflict):
        commands.revise(account_id, UUID(work['work_id']), str(uuid4()), completed['row_version'], requirement())
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('UPDATE works SET status=\'active\',row_version=row_version+1 WHERE work_id=%s', (work['work_id'],))


def test_ongoing_result_never_completes_work(commands, account_id, urls):
    work, run = advance(commands, account_id, accept(commands, account_id, completion_mode='ongoing'))
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, UUID(run['run_id']))
    lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded',
                     result=persisted_result(urls[2], account_id, run), accept_result=True)
    assert commands.get(account_id, UUID(work['work_id']))['work']['status']=='active'


def test_revision_fences_old_results_and_keeps_fixed_inputs(commands, account_id, urls, owner):
    work, run = advance(commands, account_id, accept(commands, account_id, 'research_report'))
    repository = ResearchRepository()
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, UUID(run['run_id']))
    with UnitOfWork(urls[2]) as uow:
        repository.create_plan(uow, UUID(run['run_id']), ResearchPlan('a durable mandate',
                               (ResearchQuestion('q1','question'),), SourceStrategy()))
    revised = commands.revise(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'],
                              replace(requirement('research_report'), objective='new objective')).body['work']
    assert revised['active_coordinator_run_id']==run['run_id']
    assert owner.execute('SELECT status FROM runs WHERE run_id=%s', (run['run_id'],)).fetchone()['status']=='cancelling'
    with UnitOfWork(urls[2]) as uow:
        assert repository.load_plan(uow, UUID(run['run_id']))['objective']=='a durable mandate'
    with pytest.raises(ConversationBusy):
        lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded', result=controlled_result())
    lifecycle.finish(account_id, UUID(run['run_id']), 'cancelled')
    latest = commands.get(account_id, UUID(work['work_id']))['work']
    latest, next_run = advance(commands, account_id, latest)
    assert next_run['requirement_revision']==2 and next_run['work_control_epoch']>run['work_control_epoch']


@pytest.mark.parametrize('action,interim,terminal',[('pause','pausing','paused'),('stop','stopping','stopped')])
def test_control_waits_for_cancel_convergence(commands, account_id, urls, action, interim, terminal):
    work, run = advance(commands, account_id, accept(commands, account_id))
    controlled = commands.control(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'], action).body['work']
    assert controlled['status']==interim and controlled['active_coordinator_run_id']==run['run_id']
    RunLifecycleService(urls[2]).finish(account_id, UUID(run['run_id']), 'cancelled')
    final = commands.get(account_id, UUID(work['work_id']))['work']
    assert final['status']==terminal and final['active_coordinator_run_id'] is None
    if action=='pause':
        revised = commands.revise(account_id, UUID(work['work_id']), str(uuid4()), final['row_version'], requirement()).body['work']
        assert revised['status']=='paused'
        resumed = commands.control(account_id, UUID(work['work_id']), str(uuid4()), revised['row_version'],'resume').body['work']
        assert resumed['status']=='active' and resumed['current_requirement_revision']==2


def test_failed_run_retains_responsibility_and_cancel_is_attempt_only(commands, account_id, urls):
    work, run = advance(commands, account_id, accept(commands, account_id))
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.finish(account_id, UUID(run['run_id']), 'failed', failure_code='controlled_failure')
    latest = commands.get(account_id, UUID(work['work_id']))['work']
    assert latest['status']=='active' and latest['continuation']['kind']=='blocked'
    latest, next_run = advance(commands, account_id, latest)
    CommandService(urls[1]).cancel_run(account_id, UUID(next_run['run_id']), str(uuid4()))
    latest = commands.get(account_id, UUID(work['work_id']))['work']
    assert latest['status']=='active' and latest['continuation']['reason']=='attempt_cancelled'


@pytest.mark.parametrize('status', ['running','failed','paused','stopped','completed'])
def test_illegal_work_transitions_rejected_in_database(commands, account_id, owner, status):
    work = accept(commands, account_id)
    # paused/stopped require fencing and timestamps; completed additionally requires a real receipt.
    with pytest.raises(psycopg.Error):
        owner.execute('UPDATE works SET status=%s,row_version=row_version+1 WHERE work_id=%s',
                      (status, work['work_id']))


def test_real_role_permissions_and_append_only_events(commands, account_id, urls, owner):
    work = accept(commands, account_id)
    with UnitOfWork(urls[2]) as uow:
        assert uow.execute('SELECT current_user AS name').fetchone()['name']=='hpagent_worker'
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with UnitOfWork(urls[1]) as uow:
            uow.execute('UPDATE work_requirements SET objective=\'bypass\' WHERE work_id=%s', (work['work_id'],))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with UnitOfWork(urls[2]) as uow:
            uow.execute('UPDATE work_events SET event_type=\'completed\' WHERE work_id=%s', (work['work_id'],))
    with pytest.raises(psycopg.errors.RaiseException):
        owner.execute('DELETE FROM work_events WHERE work_id=%s', (work['work_id'],))


def test_no_task_schema_or_stale_database_functions(owner):
    assert owner.execute("SELECT to_regclass('hpagent.tasks') AS name").fetchone()['name'] is None
    assert owner.execute("SELECT count(*) AS n FROM information_schema.columns WHERE table_schema='hpagent' "
                         "AND column_name='task_id'").fetchone()['n']==0
    functions = owner.execute("SELECT proname,prosrc FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                              "WHERE n.nspname='hpagent'").fetchall()
    assert not [row['proname'] for row in functions if any(token in row['prosrc'] for token in ('tasks','task_id','run_kind'))]


def test_future_once_wakeup_persists_without_executor(commands, account_id, owner):
    due = (datetime.now(UTC)+timedelta(days=1)).isoformat()
    work = accept(commands, account_id, timing={'schema_version':1,'kind':'once','timezone':'Asia/Shanghai','due_at':due})
    result = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version'])
    assert result.response_status==202 and result.body['reason']=='not_due'
    assert result.body['work']['continuation']['kind']=='at_time'
    assert owner.execute('SELECT count(*) AS n FROM work_wakeups WHERE work_id=%s AND state=\'pending\'',
                         (work['work_id'],)).fetchone()['n']==1


def test_research_query_checks_capability_and_account(commands, account_id, urls):
    _, run = advance(commands, account_id, accept(commands, account_id, 'research_report'))
    query = ResearchQueryService(urls[1])
    assert query.evidence(account_id, UUID(run['run_id']))=={'evidence':[]}
    with pytest.raises(ResourceNotFound):
        query.evidence(uuid4(), UUID(run['run_id']))
    _, other = advance(commands, account_id, accept(commands, account_id))
    with pytest.raises(ResourceNotFound):
        query.evidence(account_id, UUID(other['run_id']))
