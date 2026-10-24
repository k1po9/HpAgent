"""S5 source identity, ordinary chat isolation and capability query ownership."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import pytest

from application.main_agent import create_main_work_tools
from conversation_domain.commands import CommandService
from web_domain.errors import ResourceNotFound
from work_domain.models import Requirement

pytestmark = pytest.mark.postgres


@pytest.mark.asyncio
async def test_main_source_replay_chat_independence_and_revision(commands, account_id, urls, owner):
    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    sent = chat.send_message(account_id, cid, str(uuid4()), 'Continue in background')
    source = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s',
                           (sent['run_id'],)).fetchone()['trigger_message_id']
    context = {'account_id': str(account_id), 'conversation_id': str(cid), 'trigger_message_id': str(source)}
    tools = {t.name: t for t in create_main_work_tools(context, commands)}
    requirement = Requirement('Research then make HTML', 'generic_work', {'schema_version': 1})
    payload = {'title': 'Background', 'requirement': requirement.to_dict(), 'mandate_slot': 'goal'}
    accepted = json.loads(await tools['accept_work'].ainvoke(payload))
    work = accepted['work']
    assert accepted['handoff']['work_id'] == work['work_id']
    assert accepted['handoff']['execution_owner'] == 'background_work'
    assert '已接受不等于已完成' in accepted['handoff']['next_action']
    assert json.loads(await tools['accept_work'].ainvoke(payload))['work']['work_id'] == work['work_id']
    assert work['sources'] == [{'conversation_id': str(cid), 'source_message_id': str(source)}]
    count = owner.execute('SELECT count(*) AS n FROM messages WHERE conversation_id=%s', (cid,)).fetchone()['n']
    run = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version']).body['run']
    # Work admission neither takes the chat slot nor creates a Message.
    assert owner.execute('SELECT count(*) AS n FROM messages WHERE conversation_id=%s', (cid,)).fetchone()['n'] == count
    active = owner.execute("SELECT run_id FROM runs WHERE account_id=%s AND conversation_id=%s "
                           "AND source_kind='chat' AND status IN ('queued','running','cancelling')",
                           (account_id, cid)).fetchall()
    assert [row['run_id'] for row in active] == [UUID(sent['run_id'])]
    before = commands.get(account_id, UUID(work['work_id']))['work']
    await tools['get_work'].ainvoke({'work_id': work['work_id']})
    assert commands.get(account_id, UUID(work['work_id']))['work']['row_version'] == before['row_version']
    revised = json.loads(await tools['revise_work'].ainvoke({
        'work_id': work['work_id'], 'row_version': before['row_version'],
        'requirement': replace(requirement, objective='Use the new constraints').to_dict(),
        'operation_id': str(uuid4()),
    }))['work']
    assert revised['current_requirement_revision'] == 2
    assert owner.execute('SELECT status FROM runs WHERE run_id=%s', (run['run_id'],)).fetchone()['status'] == 'cancelling'
    assert revised['sources'] == work['sources']


def test_source_without_conversation_infers_exact_link_and_rejects_other_account(commands, account_id, urls, owner):
    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    sent = chat.send_message(account_id, cid, str(uuid4()), 'Background')
    source = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s', (sent['run_id'],)).fetchone()['trigger_message_id']
    req = Requirement('Goal', 'generic_work', {'schema_version': 1})
    work = commands.accept(account_id, str(uuid4()), 'Goal', req, source_message_id=source).body['work']
    assert work['sources'] == [{'conversation_id': str(cid), 'source_message_id': str(source)}]
    other = uuid4()
    owner.execute('INSERT INTO accounts(account_id) VALUES (%s)', (other,))
    with pytest.raises(ResourceNotFound):
        commands.accept(other, str(uuid4()), 'Goal', req, source_message_id=source)


def test_acceptance_profile_freezes_effective_work_and_run_limits(commands, account_id, owner, monkeypatch):
    from pathlib import Path

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / 'scripts' / 'acceptance'))
    from s7.composition import configure_governance

    from persistence.repositories import RunBudgetRepository
    from resources import work_budget

    monkeypatch.setattr(RunBudgetRepository, 'create_snapshot', RunBudgetRepository.create_snapshot)
    monkeypatch.setattr(work_budget, 'DEFAULT_LIMITS', dict(work_budget.DEFAULT_LIMITS))
    configure_governance({'effective_governance': {
        'work_tokens': 240000, 'work_model_calls': 32, 'generic_work_run_tokens': 160000,
        'generic_work_run_input_tokens': 128000, 'generic_work_run_output_tokens': 32000,
    }})
    work = commands.accept(account_id, str(uuid4()), 'Profile', Requirement('Goal', 'generic_work', {'schema_version': 1})).body['work']
    run = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version']).body['run']
    snapshot = owner.execute('SELECT policy_version,limits FROM run_budgets WHERE run_id=%s', (run['run_id'],)).fetchone()
    assert snapshot['policy_version'] == 'work-foundation-v1-s7-1'
    assert snapshot['limits']['model_total_tokens'] == 160000
    assert snapshot['limits']['model_input_tokens'] == 128000
    assert snapshot['limits']['model_output_tokens'] == 32000
    assert owner.execute('SELECT limits FROM work_budgets WHERE work_id=%s', (work['work_id'],)).fetchone()['limits']['model_total_tokens'] == 240000


def test_selected_authorized_directory_reaches_main_without_copying_work_grants(urls, account_id):
    from application.context_assembly import ContextAssemblyService
    from application.context_builder import HarnessContextBuilder
    from workspace.catalog import WorkspaceCatalog
    from workspace.resources import ResourcePolicy

    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    catalog = WorkspaceCatalog(urls[1])
    root = UUID(catalog.initialize(account_id)['root_id'])
    destination = catalog.create_directory(account_id, root, 'Selected destination')
    ResourcePolicy(urls[1]).grant(account_id, 'conversation', cid, destination,
        ['list_metadata', 'create_child'], False)
    sent = chat.send_message(account_id, cid, str(uuid4()), 'Save to my selected directory')
    service = ContextAssemblyService(urls[2], HarnessContextBuilder())
    base = service.load_base(account_id, UUID(sent['run_id']))
    assert base.workspace_directories == ({'directory_id': str(destination), 'name': 'Selected destination',
        'operations': ['create_child', 'list_metadata']},)
    assert str(destination) in service.compose(base, ())[0]['content']
    assert 'new Work still requires its own explicit grants' in service.compose(base, ())[0]['content']


@pytest.mark.parametrize('recover', [False, True])
async def test_main_rejected_handoff_feedback_uses_committed_receipt(urls, account_id, owner, commands, recover):
    from test_capability_facts import execution

    from web_domain.lifecycle import WebRunLifecycleService

    chat = CommandService(urls[1])
    cid = UUID(chat.create_conversation(account_id, str(uuid4())).body['conversation_id'])
    sent = chat.send_message(account_id, cid, str(uuid4()), 'Continue in background')
    run_id = UUID(sent['run_id'])
    lifecycle = WebRunLifecycleService(urls[2])
    lifecycle.prepare(run_id)
    transcript_id = str(uuid4())
    with execution(urls, account_id, run_id) as (store, segment, _):
        op = f'{run_id}:context'
        store.begin_operation(op, str(run_id), 'context')
        store.create_transcript(transcript_id=transcript_id, run_id=str(run_id), account_id=str(account_id),
            conversation_id=str(cid), execution_id=segment.execution_id, messages=[], operation_id=op)
        op = f'{run_id}:rejected'
        store.begin_operation(op, str(run_id), 'tool')
        store.complete_operation_with_event(transcript_id=transcript_id, expected_version=1, event_type='tool_result',
            operation_id=op, event_payload={'message': {'role': 'tool', 'name': 'accept_work',
            'content': json.dumps({'success': False, 'error': '委托未建立：invalid requirement'}, ensure_ascii=False)}},
            result_ref=op + ':result', result_payload={'tool_success': False})
    if recover:
        source = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s', (run_id,)).fetchone()['trigger_message_id']
        tools = {tool.name: tool for tool in create_main_work_tools({'account_id': str(account_id),
            'conversation_id': str(cid), 'trigger_message_id': str(source)}, commands)}
        await tools['accept_work'].ainvoke({'title': 'Recovered', 'mandate_slot': 'goal',
            'requirement': Requirement('Continue safely', 'generic_work', {'schema_version': 1}).to_dict()})
    from types import SimpleNamespace

    from application.main_response import execute_response
    with execution(urls, account_id, run_id) as (store, segment, _):
        op = f'{run_id}:response'
        store.begin_operation(op, str(run_id), 'model')
        version = store.complete_operation_with_event(transcript_id=transcript_id, expected_version=2,
            event_type='model_decision', operation_id=op,
            event_payload={'message': {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'answer', 'name': 'respond_directly', 'arguments': {'answer': 'Current response'}}]}},
            result_ref=op, result_payload={})
        terminal = execute_response(store, SimpleNamespace(operation_id=op, run_id=str(run_id),
            account_id=str(account_id), execution_id=segment.execution_id, transcript_id=transcript_id,
            schema_version=4), {'tool_calls': [{'tool_call_id': 'answer'}],
                'tool_call_arguments': {'answer': {'answer': 'Current response'}}, 'transcript_version': version})
    lifecycle.complete(run_id, 'Current response', terminal.decision_ref)
    message = owner.execute("SELECT content FROM messages WHERE produced_by_run_id=%s AND role='assistant'", (run_id,)).fetchone()['content']
    assert message.startswith('委托未建立') is (not recover)
