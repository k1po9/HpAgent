"""Phase 3 vertical checks using API/worker roles and durable operation receipts."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from application.main_agent import create_main_work_tools
from application.work_context import WorkContextProvider
from conversation_domain.commands import CommandService
from orchestration.run_dispatcher import ReminderActivities, RunStrategyActivities
from orchestration.run_lifecycle_contracts import RunLifecycleInput
from orchestration.work_schedule import WorkScheduleService
from run_domain.lifecycle import RunLifecycleService
from web_domain.lifecycle import WebRunLifecycleService
from work_domain.models import Requirement

pytestmark = pytest.mark.postgres


def reminder(timing=None):
    return Requirement('Remember meeting', 'reminder', {'schema_version': 1, 'content': 'Meeting'},
                       timing=timing)


def accept(commands, account_id, requirement):
    work = commands.accept(account_id, str(uuid4()), 'Scheduled Work', requirement).body['work']
    return UUID(work['work_id']), work


def once(due):
    return {'schema_version': 1, 'kind': 'once', 'timezone': 'Asia/Shanghai', 'due_at': due.isoformat()}


@pytest.mark.asyncio
async def test_reminder_due_replay_enqueues_once_and_never_completes_delivery(commands, account_id, urls, owner):
    due = datetime.now(UTC) - timedelta(minutes=1)
    work_id, work = accept(commands, account_id, reminder(once(due)))
    schedules = WorkScheduleService(urls[2])
    assert schedules.run_once() >= 1
    current = commands.get(account_id, work_id)['work']
    run_id = UUID(current['active_coordinator_run_id'])
    WebRunLifecycleService(urls[2]).prepare(run_id)
    request = RunLifecycleInput(1, str(run_id))
    strategy = await RunStrategyActivities(urls[2]).load_strategy(request)
    assert strategy['strategy_kind'] == 'deterministic'
    executor = ReminderActivities(urls[2])
    assert (await executor.execute(request))['status'] == 'succeeded'
    assert (await executor.execute(request))['status'] == 'succeeded'
    schedules.run_once()
    schedule = owner.execute('SELECT * FROM work_schedules WHERE work_id=%s', (work_id,)).fetchone()
    schedules.occurrence(account_id, work_id, schedule['schedule_version'], due)
    assert owner.execute('SELECT count(*) AS n FROM runs WHERE work_id=%s', (work_id,)).fetchone()['n'] == 1
    assert owner.execute('SELECT count(*) AS n FROM reminder_intents WHERE work_id=%s', (work_id,)).fetchone()['n'] == 1
    assert owner.execute('SELECT count(*) AS n FROM execution_result_receipts WHERE run_id=%s', (run_id,)).fetchone()['n'] == 1
    current = commands.get(account_id, work_id)['work']
    assert current['status'] == 'active' and current['continuation']['kind'] == 'awaiting_delivery'
    assert current['active_coordinator_run_id'] is None
    assert owner.execute('SELECT model_calls FROM (SELECT used->>\'model_calls\' AS model_calls FROM run_budgets WHERE run_id=%s) b', (run_id,)).fetchone()['model_calls'] is None
    commands.control(account_id, work_id, str(uuid4()), current['row_version'], 'stop')
    assert owner.execute('SELECT state FROM reminder_intents WHERE work_id=%s', (work_id,)).fetchone()['state'] == 'cancelled'


def test_daily_recovery_coalesces_history_and_preserves_schedule_version(commands, account_id, urls, owner):
    timing = {'schema_version': 1, 'kind': 'daily', 'local_time': '09:00', 'timezone': 'Asia/Shanghai'}
    req = replace(reminder(timing), completion_mode='ongoing')
    work_id, work = accept(commands, account_id, req)
    schedule = owner.execute('SELECT * FROM work_schedules WHERE work_id=%s', (work_id,)).fetchone()
    old = datetime.now(UTC) - timedelta(days=30)
    owner.execute('UPDATE work_schedules SET next_due_at=%s,applied_version=0 WHERE work_id=%s', (old, work_id))
    service = WorkScheduleService(urls[2])
    service.run_once()
    rows = owner.execute('SELECT * FROM work_schedule_occurrences WHERE work_id=%s', (work_id,)).fetchall()
    assert len(rows) == 1 and rows[0]['missed_from'] == old
    assert datetime.now(UTC) - rows[0]['scheduled_for'] < timedelta(days=1)
    active = commands.get(account_id, work_id)['work']
    commands.revise(account_id, work_id, str(uuid4()), active['row_version'], replace(req, objective='New content'))
    updated = owner.execute('SELECT * FROM work_schedules WHERE work_id=%s', (work_id,)).fetchone()
    assert updated['schedule_version'] == schedule['schedule_version']
    assert updated['requirement_revision'] == 2
    # Timing/control changes invalidate old callbacks without starting another coordinator.
    revised = commands.get(account_id, work_id)['work']
    commands.control(account_id, work_id, str(uuid4()), revised['row_version'], 'pause')
    callback = service.occurrence(account_id, work_id, schedule['schedule_version'], datetime.now(UTC) - timedelta(hours=1))
    assert callback['disposition'] == 'skipped'
    assert owner.execute('SELECT count(*) AS n FROM runs WHERE work_id=%s', (work_id,)).fetchone()['n'] == 1


def test_new_timing_revision_waits_until_new_due(commands, account_id, urls, owner):
    now = datetime.now(UTC)
    work_id, work = accept(commands, account_id, reminder(once(now-timedelta(minutes=1))))
    commands.revise(account_id, work_id, str(uuid4()), work['row_version'], reminder(once(now+timedelta(days=1))))
    WorkScheduleService(urls[2]).run_once()
    assert commands.get(account_id, work_id)['work']['active_coordinator_run_id'] is None
    assert owner.execute('SELECT count(*) AS n FROM runs WHERE work_id=%s', (work_id,)).fetchone()['n'] == 0


@pytest.mark.asyncio
async def test_generic_frozen_brief_and_progress_do_not_use_chat(commands, account_id, urls, owner):
    req = Requirement('Investigate exact scope', 'generic_work', {'schema_version': 1, 'reasoning_mode': 'react'})
    work_id, work = accept(commands, account_id, req)
    result = commands.advance(account_id, work_id, str(uuid4()), work['row_version'])
    run = result.body['run']
    run_id = UUID(run['run_id'])
    RunLifecycleService(urls[2]).start(account_id, run_id)
    loaded = await WorkContextProvider(urls[2]).load(str(run_id))
    assert loaded.conversation_id is None and loaded.session_id is None
    assert 'Investigate exact scope' in loaded.context[-1]['content']
    assert run['strategy_kind'] == 'generic_agent'
    manifest = owner.execute('SELECT context_manifest FROM run_executions WHERE run_id=%s', (run_id,)).fetchone()['context_manifest']
    assert manifest['requirement_revision'] == 1 and manifest['input_snapshot_ref'] == f'run:{run_id}'
    with pytest.raises(psycopg.Error):
        owner.execute("UPDATE runs SET input_snapshot='{}' WHERE run_id=%s", (run_id,))
    from agent_activities.fencing import fence_scope
    from agent_activities.store import AgentDataStore
    store = AgentDataStore(urls[2])
    lease = store.acquire_lease(str(account_id), str(run_id))
    operation = f'{lease.execution_id}:model:final'
    with fence_scope(str(account_id), str(run_id), lease.fencing_token, lease.execution_id):
        store.begin_operation(operation, str(run_id), 'model')
        store.complete_operation(operation, f'agent-decision:{operation}', {'content': 'Draft candidate'})
    WebRunLifecycleService(urls[2]).complete(run_id, 'Draft candidate', f'agent-decision:{operation}')
    current = commands.get(account_id, work_id)['work']
    assert current['status'] == 'active' and current['continuation']['kind'] == 'awaiting_input'
    assert current['checkpoint']['source_run_id'] == str(run_id)


@pytest.mark.asyncio
async def test_main_mandate_slot_replays_across_chat_retry(commands, account_id, urls, owner):
    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    message = chat.send_message(account_id, cid, str(uuid4()), 'Accept two ongoing mandates')
    source = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s', (message['run_id'],)).fetchone()['trigger_message_id']
    context = {'account_id': str(account_id), 'conversation_id': str(cid), 'trigger_message_id': str(source)}
    tools = {t.name: t for t in create_main_work_tools(context, commands)}
    value = {'title': 'First', 'requirement': reminder().to_dict(), 'mandate_slot': 'first'}
    first = await tools['accept_work'].ainvoke(value)
    # Fresh tool factory stands for a new chat Run/tool_call_id against the same original message.
    retry_tools = {t.name: t for t in create_main_work_tools(context, commands)}
    replay = await retry_tools['accept_work'].ainvoke(value)
    assert first == replay
    await retry_tools['accept_work'].ainvoke({**value, 'title': 'Second', 'mandate_slot': 'second'})
    assert owner.execute('SELECT count(*) AS n FROM works WHERE account_id=%s', (account_id,)).fetchone()['n'] == 2
    from application.context_assembly import ContextAssemblyService
    base = ContextAssemblyService(urls[2], object()).load_base(account_id, UUID(message['run_id']))
    assert {r['mandate_slot'] for r in base.work_command_receipts} == {'first', 'second'}


def test_generic_completion_requires_verified_evidence_and_policy(commands, account_id, urls, owner):
    import json

    from agent_activities.fencing import fence_scope
    from agent_activities.store import AgentDataStore

    req = Requirement('Complete a verified action', 'generic_work', {'schema_version': 1},
        acceptance_criteria=({'id': 'action', 'required': True, 'evidence_types': ['operation_receipt']},))
    work_id, work = accept(commands, account_id, req)
    run_id = UUID(commands.advance(account_id, work_id, str(uuid4()), work['row_version']).body['run']['run_id'])
    lifecycle = WebRunLifecycleService(urls[2])
    lifecycle.prepare(run_id)
    store = AgentDataStore(urls[2])
    lease = store.acquire_lease(str(account_id), str(run_id))
    final_ref = f'agent-decision:{lease.execution_id}:final'
    tool_ref = f'agent-tool-result:{lease.execution_id}:tool'
    with fence_scope(str(account_id), str(run_id), lease.fencing_token, lease.execution_id):
        store.begin_operation(f'{lease.execution_id}:final', str(run_id), 'model')
        store.complete_operation(f'{lease.execution_id}:final', final_ref, {'content': 'Final result'})
        store.begin_operation(f'{lease.execution_id}:tool', str(run_id), 'tool')
        store.complete_operation(f'{lease.execution_id}:tool', tool_ref, {'tool_success': True})
    result = {'schema_version': 1, 'kind': 'deliverable_ready',
              'evidence': [{'criterion_id': 'action', 'type': 'operation_receipt', 'ref': 'forged'}]}
    with pytest.raises(ValueError, match='verified tool receipt'):
        lifecycle.complete(run_id, json.dumps(result), final_ref)
    assert commands.get(account_id, work_id)['work']['status'] == 'active'
    result['evidence'][0]['ref'] = tool_ref
    lifecycle.complete(run_id, json.dumps(result), final_ref)
    current = commands.get(account_id, work_id)['work']
    assert current['status'] == 'completed'
    assert current['completion_receipt']['evaluator'] == 'work_completion_policy_v1'


def test_generic_waiting_due_commits_a_durable_continuation(commands, account_id, urls, owner):
    req = Requirement('Continue tomorrow', 'generic_work', {'schema_version': 1})
    work_id, work = accept(commands, account_id, req)
    run_id = UUID(commands.advance(account_id, work_id, str(uuid4()), work['row_version']).body['run']['run_id'])
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, run_id)
    due = datetime.now(UTC) + timedelta(days=1)
    lifecycle.finish(account_id, run_id, 'succeeded', result={'schema_version': 1, 'kind': 'waiting_due',
        'evidence': [], 'continuation': {'schema_version': 1, 'kind': 'at_time', 'reason': 'continue_tomorrow', 'due_at': due.isoformat()}})
    wake = owner.execute("SELECT * FROM work_wakeups WHERE work_id=%s AND state='pending'", (work_id,)).fetchone()
    assert wake['due_at'] == due and wake['expected_revision'] == 1
    WorkScheduleService(urls[2]).dispatch_due(now=due + timedelta(seconds=1))
    assert commands.get(account_id, work_id)['work']['active_coordinator_run_id'] is not None
