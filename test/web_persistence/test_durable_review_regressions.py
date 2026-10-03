"""Real role regressions for the five Durable Work implementation review findings."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from support.qq_messages import qq_message

from agent_activities.fencing import fence_scope
from agent_activities.store import AgentDataStore
from application.main_agent import create_main_work_tools
from conversation_domain.commands import CommandService
from orchestration.work_schedule import WorkScheduleService, run_work_schedule_loop
from persistence.uow import UnitOfWork
from resources.model_budget_coordinator import ModelBudgetCoordinator
from resources.run_budget import RunBudgetExhausted, RunBudgetService
from run_domain.lifecycle import RunLifecycleService
from sandbox.sandbox_manager import SandboxManager
from work_domain.commands import WorkCommandService
from work_domain.models import Requirement, continuation
from workspace.execution import ExecutionResourceService

from .test_qq_canonical_ingress import bind
from .test_qq_canonical_ingress import service as qq_service

pytestmark = pytest.mark.postgres


def accept(database, account, **changes):
    commands = WorkCommandService(database)
    work = commands.accept(account, str(uuid4()), 'PRIVATE_TITLE', Requirement(
        'PRIVATE_OBJECTIVE', 'generic_work', {'schema_version': 1}, **changes
    )).body['work']
    return commands, work


def advance(commands, account, work):
    return commands.advance(account, UUID(work['work_id']), str(uuid4()), work['row_version']).body['run']


def progress(kind='ready', **details):
    return {'schema_version': 1, 'kind': 'progress_saved', 'evidence': [],
            'continuation': continuation(kind, 'PRIVATE_CONTINUATION', **details)}


@pytest.mark.asyncio
@pytest.mark.parametrize('scope', ['group', 'private', 'web'])
async def test_ingress_execution_main_tools_preserve_audience(
    db, account_id, database_url, worker_database_url, tmp_path, scope
):
    commands, private = accept(database_url, account_id)
    if scope == 'web':
        chat = CommandService(database_url)
        cid = UUID(chat.create_conversation(account_id, str(uuid4()))['conversation_id'])
        ingress = chat.send_message(account_id, cid, str(uuid4()), 'Inspect Work')
    else:
        bind(db, account_id)
        ingress = await qq_service(worker_database_url).accept(qq_message(scope=scope), 'napcat')
        cid = UUID(ingress['conversation_id'])
    run_id = UUID(ingress['run_id'])
    chat = CommandService(worker_database_url)
    chat.start_run(account_id, run_id)
    store = AgentDataStore(worker_database_url)
    lease = store.acquire_lease(str(account_id), str(run_id))
    manager = SandboxManager(native_tools_enabled=False, nsjail_enabled=False,
                             work_commands=WorkCommandService(worker_database_url))
    resources = ExecutionResourceService(worker_database_url, manager, execution_root=tmp_path)

    def verify(encoded):
        value = json.loads(encoded)
        if scope == 'group':
            assert 'PRIVATE_' not in encoded
            works = value.get('items', [value.get('work')])
            for work in works:
                assert set(work) == {'work_id', 'status', 'row_version',
                                     'current_requirement_revision', 'continuation'}
                assert set(work['continuation']) == {'kind'}
        return value

    with fence_scope(str(account_id), str(run_id), lease.fencing_token, lease.execution_id):
        async with resources.lease_for_run(account_id, run_id):
            sandbox = manager.get_sandbox_for_execution(f'{lease.execution_id}:{lease.fencing_token}')
            tools = {tool.name: tool for tool in sandbox._registry.list_all()}
            listed = verify(await tools['list_works'].ainvoke({}))
            detail = verify(await tools['get_work'].ainvoke({'work_id': private['work_id']}))
            if scope != 'group':
                assert listed['items'][0]['title'] == 'PRIVATE_TITLE'
                assert detail['work']['requirement']['objective'] == 'PRIVATE_OBJECTIVE'
            work = verify(await tools['accept_work'].ainvoke({
                'title': 'PRIVATE_ACCEPT', 'requirement': Requirement(
                    'PRIVATE_ACCEPT', 'generic_work', {'schema_version': 1}).to_dict(),
                'mandate_slot': 'review',
            }))['work']
            for name, arguments in (
                ('revise_work', {'requirement': Requirement('PRIVATE_REVISED', 'generic_work',
                                                           {'schema_version': 1}).to_dict()}),
                ('control_work', {'action': 'pause'}),
                ('control_work', {'action': 'resume'}),
                ('link_work', {}),
                ('advance_work', {}),
            ):
                work = verify(await tools[name].ainvoke({
                    'work_id': work['work_id'], 'row_version': work['row_version'],
                    'operation_id': str(uuid4()), **arguments,
                }))['work']
    assert commands.get(account_id, UUID(private['work_id']))['work']['title'] == 'PRIVATE_TITLE'


@pytest.mark.asyncio
async def test_guild_tools_use_public_projection():
    class Commands:
        def get(self, *args):
            return {'work': {'work_id': 'id', 'status': 'active', 'row_version': 1,
                             'current_requirement_revision': 1, 'title': 'PRIVATE_TITLE',
                             'continuation': continuation('blocked', 'PRIVATE_REASON', operation_ref='PRIVATE_REF')}}
    tools = create_main_work_tools({'account_id': str(uuid4()), 'conversation_id': str(uuid4()),
                                   'trigger_message_id': str(uuid4()), 'scope': 'guild'}, Commands())
    result = await next(t for t in tools if t.name == 'get_work').ainvoke({'work_id': str(uuid4())})
    assert 'PRIVATE_' not in result


@pytest.mark.parametrize('next_trigger', ['advance', 'daily'])
def test_retry_chain_ends_at_success_and_new_occurrence(
    db, account_id, database_url, worker_database_url, next_trigger
):
    timing = ({'schema_version': 1, 'kind': 'daily', 'timezone': 'UTC', 'local_time': '00:00'}
              if next_trigger == 'daily' else None)
    commands, work = accept(database_url, account_id, timing=timing, completion_mode='ongoing')
    scheduler = WorkScheduleService(worker_database_url)
    if next_trigger == 'daily':
        schedule = db.execute('SELECT schedule_version FROM work_schedules WHERE work_id=%s',
                              (work['work_id'],)).fetchone()[0]
        due = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        scheduler.occurrence(account_id, UUID(work['work_id']), schedule, due)
        scheduler.dispatch_due()
        work = commands.get(account_id, UUID(work['work_id']))['work']
        with UnitOfWork(worker_database_url) as uow:
            run = dict(uow.execute('SELECT * FROM runs WHERE run_id=%s', (work['active_coordinator_run_id'],)).fetchone())
    else:
        run = advance(commands, account_id, work)
    lifecycle = RunLifecycleService(worker_database_url)
    for _ in range(2):
        lifecycle.finish(account_id, UUID(str(run['run_id'])), 'failed', failure_code='controlled_failure')
        current = commands.get(account_id, UUID(work['work_id']))['work']
        retry = advance(commands, account_id, current)
        assert retry['retry_of_run_id'] == str(run['run_id'])
        run = retry
    lifecycle.start(account_id, UUID(run['run_id']))
    result = (progress('at_time', due_at=(due+timedelta(days=1)).isoformat())
              if next_trigger == 'daily' else progress())
    lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded', result=result)
    current = commands.get(account_id, UUID(work['work_id']))['work']
    if next_trigger == 'daily':
        scheduler.run_once(now=due+timedelta(days=1, minutes=1))
        next_id = commands.get(account_id, UUID(work['work_id']))['work']['active_coordinator_run_id']
        assert next_id
        next_run = db.execute('SELECT retry_of_run_id FROM runs WHERE run_id=%s', (next_id,)).fetchone()
        assert next_run == (None,)
    else:
        assert advance(commands, account_id, current)['retry_of_run_id'] is None


def test_admission_failure_is_local_and_pending_wakeup_recovers(
    db, account_id, database_url, worker_database_url, monkeypatch
):
    import orchestration.work_schedule as module

    commands, broken = accept(database_url, account_id)
    other = uuid4()
    db.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
    _, healthy = accept(database_url, other)
    original = module.admit_work_run

    def admit(uow, work, *args):
        if str(work['work_id']) == broken['work_id']:
            uow.execute('SELECT 1/0')  # A real PG failure aborts the savepoint.
        return original(uow, work, *args)

    scheduler = WorkScheduleService(worker_database_url)
    with monkeypatch.context() as patch:
        patch.setattr(module, 'admit_work_run', admit)
        assert scheduler.dispatch_due() == 1
    assert commands.get(other, UUID(healthy['work_id']))['work']['active_coordinator_run_id']
    assert commands.get(account_id, UUID(broken['work_id']))['work']['active_coordinator_run_id'] is None
    assert scheduler.dispatch_due() == 1


@pytest.mark.asyncio
async def test_schedule_loop_recovers_iteration_failure_and_propagates_cancel(monkeypatch):
    calls = 0

    class Service:
        def run_once(self):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise psycopg.OperationalError('lost connection')

    async def sleep(_):
        if calls == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr('orchestration.work_schedule.asyncio.sleep', sleep)
    with pytest.raises(asyncio.CancelledError):
        await run_work_schedule_loop(Service())
    assert calls == 2


@pytest.mark.parametrize('action', ['none', 'pause', 'stop', 'revise', 'complete'])
def test_ready_wakeup_is_deduplicated_and_obeys_latest_work(
    db, account_id, database_url, worker_database_url, action
):
    criteria = (({'id': 'action', 'required': True, 'evidence_types': ['operation_receipt']},)
                if action == 'complete' else ())
    commands, work = accept(database_url, account_id, acceptance_criteria=criteria)
    run = advance(commands, account_id, work)
    lifecycle = RunLifecycleService(worker_database_url)
    lifecycle.start(account_id, UUID(run['run_id']))
    result = progress()
    if action == 'complete':
        store = AgentDataStore(worker_database_url)
        lease = store.acquire_lease(str(account_id), run['run_id'])
        operation = lease.execution_id + ':verified'
        with fence_scope(str(account_id), run['run_id'], lease.fencing_token, lease.execution_id):
            store.begin_tool_operation(operation, run['run_id'])
            store.complete_operation(operation, 'operation:' + operation,
                                     {'tool_success': True, 'side_effect_class': 'read_only'})
        result['evidence'] = [{'criterion_id': 'action', 'type': 'operation_receipt',
                               'ref': 'operation:' + operation}]
    lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded', result=result,
                     accept_result=action == 'complete')
    lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded', result=result)
    assert db.execute("SELECT count(*) FROM work_wakeups WHERE work_id=%s AND state='pending'",
                      (work['work_id'],)).fetchone() == (0 if action == 'complete' else 1,)
    current = commands.get(account_id, UUID(work['work_id']))['work']
    if action in {'pause', 'stop'}:
        commands.control(account_id, UUID(work['work_id']), str(uuid4()), current['row_version'], action)
    elif action == 'revise':
        commands.revise(account_id, UUID(work['work_id']), str(uuid4()), current['row_version'],
                        Requirement('New goal', 'generic_work', {'schema_version': 1}))
    WorkScheduleService(worker_database_url).dispatch_due()
    runs = db.execute('SELECT requirement_revision,input_snapshot FROM runs WHERE work_id=%s ORDER BY created_at',
                      (work['work_id'],)).fetchall()
    if action == 'none':
        assert len(runs) == 2 and runs[1][1]['checkpoint'] == current['checkpoint']
    elif action == 'revise':
        assert len(runs) == 2 and runs[1][0] == 2
    else:
        assert len(runs) == 1


@pytest.mark.parametrize('kind', ['awaiting_input', 'blocked', 'none'])
def test_success_does_not_autorun_waiting_work(
    db, account_id, database_url, worker_database_url, kind
):
    commands, work = accept(database_url, account_id)
    run = advance(commands, account_id, work)
    lifecycle = RunLifecycleService(worker_database_url)
    lifecycle.start(account_id, UUID(run['run_id']))
    lifecycle.finish(account_id, UUID(run['run_id']), 'succeeded', result=progress(kind))
    assert WorkScheduleService(worker_database_url).dispatch_due() == 0


def test_41st_tool_rejection_leaves_no_work_hold_but_preserves_unknown_usage(
    db, account_id, database_url, worker_database_url
):
    commands, work = accept(database_url, account_id)
    run = advance(commands, account_id, work)
    rid = UUID(run['run_id'])
    budget = RunBudgetService(worker_database_url)
    budget.reserve(rid, 'unknown-call', {'bytes_scanned': 100})
    for index in range(40):
        budget.reserve(rid, f'tool-{index}', {'tool_calls': 1})
        budget.settle(rid, f'tool-{index}', {'tool_calls': 1}, 'measured')
    for _ in range(2):
        with pytest.raises(RunBudgetExhausted):
            budget.reserve(rid, 'rejected', {'tool_calls': 1})
    assert db.execute('SELECT status FROM run_budgets WHERE run_id=%s', (rid,)).fetchone() == ('exhausted',)
    for table in ('work_usage_ledger', 'run_usage_ledger'):
        assert db.execute(f'SELECT count(*) FROM {table} WHERE run_id=%s AND operation_id=%s',
                          (rid, 'rejected')).fetchone() == (0,)
    RunLifecycleService(worker_database_url).finish(account_id, rid, 'failed', failure_code='controlled_failure')
    for table, key in (('work_budgets', 'work_id'), ('run_budgets', 'run_id')):
        reserved = db.execute(f'SELECT reserved FROM {table} WHERE {key}=%s',
                              (work['work_id'] if key == 'work_id' else rid,)).fetchone()[0]
        assert reserved.get('tool_calls', 0) == 0 and reserved['bytes_scanned'] == 100
    budget.settle(rid, 'unknown-call', {'bytes_scanned': 23}, 'measured')
    assert commands.get(account_id, UUID(work['work_id']))['work']['budget']['reserved']['bytes_scanned'] == 0


def test_model_rejection_rolls_back_all_three_budget_scopes(
    db, account_id, database_url, worker_database_url
):
    db.execute("INSERT INTO account_entitlements(account_id,model_access_tier,daily_token_limit,prompt_visibility) "
               "VALUES (%s,'standard',1000000,'summary')", (account_id,))
    commands, work = accept(database_url, account_id)
    run = advance(commands, account_id, work)
    rid = UUID(run['run_id'])
    coordinator = ModelBudgetCoordinator(worker_database_url)
    with pytest.raises(RunBudgetExhausted):
        coordinator.reserve(account_id, rid, 'rejected-model', 100001,
                            {'model_total_tokens': 100001, 'model_calls': 1})
    for table in ('work_usage_ledger', 'run_usage_ledger', 'account_model_usage_ledger'):
        assert db.execute(f'SELECT count(*) FROM {table} WHERE operation_id=%s',
                          ('rejected-model',)).fetchone() == (0,)
    mutation = coordinator.reserve(account_id, rid, 'legal-model', 10,
                                   {'model_total_tokens': 10, 'model_calls': 1})
    coordinator.settle(account_id, rid, 'legal-model', 7,
                       {'model_total_tokens': 7, 'model_calls': 1}, 'provider',
                       quota_date=mutation.account.quota_date)
    assert commands.get(account_id, UUID(work['work_id']))['work']['budget']['used']['model_total_tokens'] == 7


def test_parallel_reservations_enforce_run_limit_without_orphan_holds(
    db, account_id, database_url, worker_database_url
):
    commands, work = accept(database_url, account_id)
    rid = UUID(advance(commands, account_id, work)['run_id'])
    db.execute("UPDATE run_budgets SET limits=jsonb_set(limits,'{tool_calls}','1') WHERE run_id=%s", (rid,))
    budget = RunBudgetService(worker_database_url)

    def reserve(index):
        try:
            budget.reserve(rid, f'branch-{index}', {'tool_calls': 1})
            return True
        except RunBudgetExhausted:
            return False

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(reserve, range(3))) == 1
    assert db.execute('SELECT reserved FROM work_budgets WHERE work_id=%s',
                      (work['work_id'],)).fetchone()[0] == {'tool_calls': 1}
    assert db.execute('SELECT reserved FROM run_budgets WHERE run_id=%s', (rid,)).fetchone()[0] == {'tool_calls': 1}


@pytest.mark.asyncio
async def test_generic_file_tools_and_subagent_read_use_finite_shared_budgets(
    db, account_id, database_url, worker_database_url, tmp_path
):
    from agent_activities.delegation import DelegationActivities
    from agent_workflows.contracts import CompactToolCall, RunContext, RunSource, ToolExecutionInput
    from agent_workflows.delegation_contracts import DelegationInput
    from agent_workflows.lifecycle_contracts import SegmentInput
    from file_runtime import OutputPublisher
    from sandbox.tools.local.file_analysis import create_file_analysis_tools
    from sandbox.tools.local.file_read import create_file_read_tools
    from sandbox.tools.local.file_write import create_file_write_tools
    from storage.tenant_file_store import TenantFileStore
    from web_domain.file_services import FileService
    from workspace.catalog import WorkspaceCatalog
    from workspace.file_scope import RunFileWorkspace
    from workspace.resources import ResourcePolicy

    file_store = TenantFileStore(tmp_path / 'files', max_bytes=1024 * 1024)
    files = FileService(database_url, file_store, max_bytes=1024 * 1024)
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n['node_id'] for n in tree['nodes'] if n['name'] == '资料'))
    body = b'Authorized evidence\nsecond line\n'
    fid = UUID(files.create_workspace_upload(account_id, str(uuid4()), 'evidence.txt',
                                             len(body), 'text/plain', None)['file']['file_id'])

    async def chunks():
        yield body

    await files.upload_content(account_id, fid, chunks())
    node = catalog.save_file(account_id, parent, fid, 'evidence.txt', 'save:evidence')
    commands, work = accept(database_url, account_id)
    ResourcePolicy(database_url).grant(account_id, 'work', UUID(work['work_id']), node,
                                       ['list_metadata', 'read_content'], False)
    run = advance(commands, account_id, work)
    rid = UUID(run['run_id'])
    RunLifecycleService(worker_database_url).start(account_id, rid)
    agent = AgentDataStore(worker_database_url)
    root = agent.run_identity(run['run_id'])['execution_id']
    seg = SegmentInput(2, run['run_id'], str(account_id), str(uuid4()), execution_id=root)
    token = agent.acquire_segment(seg)
    budget = RunBudgetService(worker_database_url)
    workspace = RunFileWorkspace(worker_database_url, file_store, tmp_path / 'executions')
    publisher = OutputPublisher(worker_database_url, file_store)

    async def execute(tool, arguments, operation):
        reservation = tool.metadata['budget_reservation']
        budget.reserve(rid, operation, reservation)
        result = json.loads(await tool.ainvoke(arguments))
        actual = {key: 1 if key == 'tool_calls' else 0 for key in reservation}
        for field, key in tool.metadata['usage_json_fields'].items():
            if key in actual:
                actual[key] = result.get(field, 0)
        budget.settle(rid, operation, actual, 'measured')
        return result

    with fence_scope(str(account_id), run['run_id'], token, root):
        with workspace.prepare(account_id, rid) as scope:
            scope.select(node)
            tools = {t.name: t for t in create_file_read_tools(lambda: scope)
                     + create_file_analysis_tools(lambda: scope)
                     + create_file_write_tools(lambda: scope, publisher)}
            read = await execute(tools['read_file'], {'file': 'evidence.txt'}, 'root:read')
            assert 'Authorized evidence' in json.dumps(read)
            await execute(tools['text_stats'], {'file': 'evidence.txt'}, 'root:analysis')
            for name, arguments in (
                ('create_docx', {'output_name': 'report.docx', 'blocks': [{'text': 'Small report'}]}),
                ('create_workbook', {'output_name': 'report.xlsx',
                                     'sheets': [{'name': 'Evidence', 'rows': [['count', 2]]}]}),
            ):
                output = await execute(tools[name], {**arguments, 'operation_id': name}, 'root:' + name)
                assert 0 < output['size_bytes'] < 100000
            brief = {'branch_key': 'read', 'objective': 'Read authorized evidence', 'constraints': [],
                     'output_contract': 'Evidence summary', 'node_ids': [str(node)], 'file_ids': [],
                     'tool_names': ['read_file'], 'required': False}
            decision = root + ':decision'
            ref = 'agent-decision:' + decision
            agent.begin_operation(decision, run['run_id'], 'model')
            agent.complete_operation(decision, ref, {'content': 'Read', 'delegatable_tools': ['read_file']})
            op = root + ':delegate'
            agent.begin_tool_operation(op, run['run_id'])
            tool_input = ToolExecutionInput(2, run['run_id'], str(account_id), RunSource('work', work['work_id']),
                                           RunContext(surface='work'), 'react', 'unused', 1, 1, op, 0,
                                           CompactToolCall('call', 'delegate_work', ref + '#call'), execution_id=root)
            request = DelegationInput(tool_input, execution_id=root, account_id=str(account_id),
                                      run_id=run['run_id'], operation_id=op)
            child = DelegationActivities(agent, budget)._prepare(request, [brief])[0]
    agent.release_segment(seg)
    child_seg = SegmentInput(2, run['run_id'], str(account_id), str(uuid4()), execution_id=child.execution_id)
    child_token = agent.acquire_segment(child_seg)
    with fence_scope(str(account_id), run['run_id'], child_token, child.execution_id):
        with workspace.prepare(account_id, rid) as scope:
            scope.select(node)
            tool = next(t for t in create_file_read_tools(lambda: scope) if t.name == 'read_file')
            await execute(tool, {'file': 'evidence.txt'}, 'child:read')
            with pytest.raises(RunBudgetExhausted):
                budget.reserve(rid, 'child:over-limit', {'bytes_scanned': 1024 * 1024 * 1024 + 1})
    agent.release_segment(child_seg)
    usage = db.execute('SELECT used,reserved FROM run_budgets WHERE run_id=%s', (rid,)).fetchone()
    work_usage = db.execute('SELECT used,reserved FROM work_budgets WHERE work_id=%s',
                           (work['work_id'],)).fetchone()
    assert usage == work_usage and usage[0]['tool_calls'] == 5
    assert all(value == 0 for value in usage[1].values())
    assert db.execute("SELECT execution_id FROM run_usage_ledger WHERE run_id=%s AND operation_id='child:read'",
                      (rid,)).fetchone()[0] == UUID(child.execution_id)
    assert db.execute("SELECT count(*) FROM stored_files WHERE source_run_id=%s AND purpose='output'",
                      (rid,)).fetchone() == (2,)
