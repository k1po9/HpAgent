"""Actual Main decisions/receipts with a deterministic Brain, isolated PostgreSQL."""
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID, uuid4

import pytest
from temporalio.exceptions import ApplicationError
from test_capability_facts import chat, execution
from test_delivery_contract_unit import requirement

from actions.contracts import ActionRequest
from agent_activities.runtime import DurableAgentActivities
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    ModelDecisionInput,
    RunContext,
    RunSource,
)
from application.main_agent import create_main_work_tools
from brain.contracts import BrainDecision
from conversation_domain.execution_bindings import ChatExecutionBindings
from resources.run_budget import RunBudgetService
from web_domain.lifecycle import WebRunLifecycleService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]

UNVERIFIED_RESPONSE = (
    '我目前无法核实这项业务操作是否已成功。请先检查对应任务或文件的实际状态，'
    '再决定是否重新发起，以免重复操作。'
)


def context(store, run, account_id, segment):
    tid = f't:{run}'
    store.begin_operation(f'context:{run}', str(run), 'context')
    store.create_transcript(transcript_id=tid, run_id=str(run), account_id=str(account_id),
        execution_id=segment.execution_id, conversation_id=str(store.run_identity(str(run))['conversation_id']),
        session_id=store.run_identity(str(run))['session_id'],
        messages=[{'role': 'user', 'content': 'Background task'}], operation_id=f'context:{run}')
    return tid


def model_input(run, account_id, segment, token, tid, turn=1, version=1, final=False):
    return ModelDecisionInput(AGENT_SCHEMA_VERSION, str(run), str(account_id), RunSource('chat', str(uuid4())),
        RunContext(), 'react', tid, version, turn, f'decision:{run}:{turn}', token,
        execution_id=segment.execution_id, final_only=final)


def runtime(store, responses):
    brain = SimpleNamespace(generate_chat_decision=AsyncMock(side_effect=responses), generate_final_decision=AsyncMock())
    actions = SimpleNamespace(reset_execution=Mock(), clear_execution=Mock(),
        select_tools=AsyncMock(return_value=[]), side_effect_class=Mock(return_value='read_only'))
    events = SimpleNamespace(progress=AsyncMock(), trace_start=AsyncMock(), trace_end=AsyncMock(), close=AsyncMock())
    obj = DurableAgentActivities(context_bindings=ChatExecutionBindings(), store=store, loader=None, brain=brain,
        actions=actions, event_factory=SimpleNamespace(for_run=lambda _: events), resource_prep=None,
        lifecycle=WebRunLifecycleService(store.database_url), run_budget=RunBudgetService(store.database_url))
    @asynccontextmanager
    async def workspace(*a):
        yield
    obj._workspace = workspace
    return obj


def respond(answer='普通回答', refs=None):
    args = {'answer': answer}
    if refs is not None:
        args['command_refs'] = refs
    return BrainDecision('', [ActionRequest('answer', 'respond_directly', args)], 'tool_calls', {})


async def accept_receipt(owner, commands, account_id, run):
    identity = owner.execute('SELECT * FROM runs WHERE run_id=%s', (run,)).fetchone()
    tools = create_main_work_tools({'account_id': str(account_id),
        'conversation_id': str(identity['conversation_id']),
        'trigger_message_id': str(identity['trigger_message_id'])}, commands)
    accept = next(t for t in tools if t.name == 'accept_work')
    output = await accept.ainvoke({'title': 'Response claims',
        'requirement': requirement().to_dict(), 'mandate_slot': 'one'})
    return output, json.loads(output)['command_receipt']['command_id']


def observe_receipt(store, run, tid, version, output, *, success=True, name='accept_work'):
    op = f'tool:{run}'
    store.begin_operation(op, str(run), 'tool')
    return store.complete_operation_with_event(transcript_id=tid, expected_version=version,
        event_type='tool_result', operation_id=op,
        event_payload={'message': {'role': 'tool', 'name': name,
            'tool_call_id': 'accept', 'content': output},
            'raw_result': {'success': success, 'output': output}},
        result_ref=op, result_payload={'output': output})


@pytest.mark.parametrize(('answer', 'blocked'), [
    ('根据已保存的记忆，地点是桂雨楼。', False),
    ('我记得你之前去过桂雨楼。', False),
    ('我没有保存这份文件。', False),
    ('如果任务创建成功，系统会通知你。', False),
    ('用户提到了‘任务已提交’这句话。', False),
    ('用户提到任务已经提交。', False),
    ('我已经为你创建了一个后台任务。', True),
    ('后台任务已创建成功。', True),
    ('后台任务已经提交。', True),
    ('我已经把报告保存到工作空间。', True),
    ('报告保存成功。', True),
    ('用户提到了“任务已提交”这句话，但报告保存成功。', True),
    ('用户说“任务已提交”而我已保存“报告”。', True),
])
async def test_response_claims_without_receipts_preserve_or_replace_final_content(
        urls, account_id, owner, answer, blocked):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        obj = runtime(store, [respond(answer)])
        req = model_input(run, account_id, segment, token, tid)
        terminal = await obj.model_decision(req)
        assert terminal.decision_type == 'final'
        assert await obj.model_decision(req) == terminal
        assert obj.brain.generate_chat_decision.await_count == 1
        obj.brain.generate_final_decision.assert_not_awaited()
    expected = UNVERIFIED_RESPONSE if blocked else answer
    payload = owner.execute('SELECT result_payload FROM execution_operations WHERE operation_id=%s',
        (terminal.operation_id,)).fetchone()['result_payload']
    assert payload['content'] == expected
    assert terminal.display_summary == expected
    obj.lifecycle.complete(run, 'untrusted replacement', terminal.decision_ref)
    assert owner.execute('SELECT content FROM messages WHERE produced_by_run_id=%s',
        (run,)).fetchone()['content'] == expected


@pytest.mark.parametrize(('action', 'answer'), [
    ('pause', '后台任务已暂停。'),
    ('stop', '后台任务已停止。'),
])
async def test_matching_control_receipt_supports_the_corresponding_claim(
        urls, account_id, owner, commands, action, answer):
    run = chat(urls, account_id)
    output, _ = await accept_receipt(owner, commands, account_id, run)
    work = json.loads(output)['work']
    identity = owner.execute('SELECT * FROM runs WHERE run_id=%s', (run,)).fetchone()
    tools = create_main_work_tools({'account_id': str(account_id),
        'conversation_id': str(identity['conversation_id']),
        'trigger_message_id': str(identity['trigger_message_id'])}, commands)
    control = next(t for t in tools if t.name == 'control_work')
    output = await control.ainvoke({'work_id': work['work_id'], 'row_version': work['row_version'],
        'action': action, 'operation_id': f'control:{run}'})
    cid = json.loads(output)['command_receipt']['command_id']
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        version = observe_receipt(store, run, tid, 1, output, name='control_work')
        obj = runtime(store, [respond(answer, [cid])])
        terminal = await obj.model_decision(model_input(run, account_id, segment, token,
            tid, version=version))
    assert terminal.decision_type == 'final'
    payload = owner.execute('SELECT result_payload FROM execution_operations WHERE operation_id=%s',
        (terminal.operation_id,)).fetchone()['result_payload']
    assert payload['content'].endswith('\n\n' + answer)
    assert f'已核验命令 {action}_work' in payload['content']


@pytest.mark.parametrize(('answer', 'blocked'), [
    ('后台任务已创建成功。', False),
    ('后台任务已创建成功，报告保存成功。', True),
    ('我已经把报告保存到工作空间。', True),
    ('文件已上传。', True),
    ('任务已修改。', True),
    ('任务已暂停。', True),
    ('任务已完成。', True),
    ('消息已发送。', True),
])
async def test_accept_receipt_only_supports_work_acceptance(
        urls, account_id, owner, commands, answer, blocked):
    run = chat(urls, account_id)
    output, cid = await accept_receipt(owner, commands, account_id, run)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        version = observe_receipt(store, run, tid, 1, output)
        obj = runtime(store, [respond(answer, [cid])])
        terminal = await obj.model_decision(model_input(run, account_id, segment, token,
            tid, version=version))
        assert terminal.decision_type == 'final'
        assert obj.brain.generate_chat_decision.await_count == 1
        obj.brain.generate_final_decision.assert_not_awaited()
    payload = owner.execute('SELECT result_payload FROM execution_operations WHERE operation_id=%s',
        (terminal.operation_id,)).fetchone()['result_payload']
    assert payload['response_receipt']['business_commands'][0]['command_id'] == cid
    if blocked:
        assert payload['content'] == UNVERIFIED_RESPONSE
    else:
        assert payload['content'].endswith('\n\n' + answer)
        assert '已核验命令 accept_work' in payload['content']


@pytest.mark.parametrize('source', ['other_account', 'other_run', 'unobserved', 'failed_tool'])
async def test_response_rejects_foreign_or_unobserved_receipts(
        urls, account_id, owner, commands, source):
    source_account = account_id
    if source == 'other_account':
        source_account = uuid4()
        owner.execute('INSERT INTO accounts(account_id) VALUES (%s)', (source_account,))
    source_run = chat(urls, source_account)
    output, cid = await accept_receipt(owner, commands, source_account, source_run)
    if source != 'unobserved':
        # Observe the genuine receipt in its source Run only.
        with execution(urls, source_account, source_run) as (store, segment, _):
            tid = context(store, source_run, source_account, segment)
            observe_receipt(store, source_run, tid, 1, output)
    run = source_run if source == 'unobserved' else chat(urls, account_id)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        version = 1
        if source in {'other_account', 'failed_tool'}:
            # Even a foreign ID injected into this transcript cannot pass the
            # authoritative account filter; failed tool results grant no refs.
            version = observe_receipt(store, run, tid, version, output,
                success=source != 'failed_tool')
        obj = runtime(store, [respond('后台任务已创建成功。', [cid])])
        rejected = await obj.model_decision(model_input(run, account_id, segment, token,
            tid, version=version))
        assert rejected.decision_type == 'tool_calls' and not rejected.tool_calls
        assert rejected.stop_reason == 'invalid_response_action'
        assert obj.brain.generate_chat_decision.await_count == 1
        obj.brain.generate_final_decision.assert_not_awaited()


@pytest.mark.parametrize('initial', [BrainDecision('已经委托后台任务', [], 'end_turn', {}),
    BrainDecision('<tool_call>accept_work</tool_call>', [], 'stop', {}),
    BrainDecision('', [ActionRequest('a', 'accept_work', {}), ActionRequest('b', 'respond_directly', {'answer': 'done'})], 'tool_calls', {})])
async def test_plain_or_fake_or_parallel_actions_do_not_end_or_execute(urls, account_id, owner, initial):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        obj = runtime(store, [initial, respond()])
        req = model_input(run, account_id, segment, token, tid)
        result = await obj.model_decision(req)
        assert result.decision_type == 'tool_calls' and not result.tool_calls
        with pytest.raises(ValueError, match='genuine respond_directly'):
            obj.lifecycle.complete(run, 'forged success', result.decision_ref)
        terminal = await obj.model_decision(replace(req, turn=2, operation_id=f'decision:{run}:2', transcript_version=result.transcript_version))
        assert terminal.decision_type == 'final'
        assert obj.brain.generate_chat_decision.await_count == 2
        assert obj.brain.generate_final_decision.await_count == 0
    obj.lifecycle.complete(run, 'untrusted replacement', terminal.decision_ref)
    assert owner.execute('SELECT content FROM messages WHERE produced_by_run_id=%s', (run,)).fetchone()['content'] == '普通回答'
    assert not owner.execute('SELECT 1 FROM works WHERE account_id=%s', (account_id,)).fetchone()


@pytest.mark.parametrize('final', [False, True])
async def test_terminal_receipt_is_idempotent_and_is_not_a_business_command(urls, account_id, owner, final):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        obj = runtime(store, [respond('已经提交后台任务')])
        req = model_input(run, account_id, segment, token, tid, final=final)
        result = await obj.model_decision(req)
        again = await obj.model_decision(req)
        assert result == again and obj.brain.generate_chat_decision.await_count == 1
        args = obj.brain.generate_chat_decision.call_args.kwargs
        assert 'respond_directly' in {t['function']['name'] for t in args['tools']}
        assert obj.brain.generate_final_decision.await_count == 0
    row = owner.execute('SELECT result_payload FROM execution_operations WHERE operation_id=%s', (req.operation_id+':respond',)).fetchone()['result_payload']
    assert row['response_receipt'] == {'kind': 'response_terminated', 'business_commands': []}
    assert row['content'] == UNVERIFIED_RESPONSE
    assert not owner.execute('SELECT 1 FROM works WHERE account_id=%s', (account_id,)).fetchone()


async def test_business_receipt_is_committed_same_run_command_and_repair_is_multi_turn(urls, account_id, owner, commands):
    run = chat(urls, account_id)
    identity = owner.execute('SELECT * FROM runs WHERE run_id=%s', (run,)).fetchone()
    tools = create_main_work_tools({'account_id': str(account_id), 'conversation_id': str(identity['conversation_id']),
        'trigger_message_id': str(identity['trigger_message_id'])}, commands)
    accept = next(t for t in tools if t.name == 'accept_work')
    output = await accept.ainvoke({'title': 'Formats', 'requirement': requirement().to_dict(), 'mandate_slot': 'one'})
    value = json.loads(output)
    cid = value['command_receipt']['command_id']
    assert owner.execute("SELECT 1 FROM idempotency_commands WHERE idempotency_command_id=%s AND status='completed'", (UUID(cid),)).fetchone()
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        # Same account command not observed in this Run must be rejected.
        obj = runtime(store, [respond('done', [cid]), respond('收到真实回执', [cid])])
        req = model_input(run, account_id, segment, token, tid)
        rejected = await obj.model_decision(req)
        assert rejected.decision_type == 'tool_calls'
        op = f'tool:{run}'
        store.begin_operation(op, str(run), 'tool')
        version = store.complete_operation_with_event(transcript_id=tid, expected_version=rejected.transcript_version,
            event_type='tool_result', operation_id=op,
            event_payload={'message': {'role': 'tool', 'name': 'accept_work', 'tool_call_id': 'accept', 'content': output},
                           'raw_result': {'success': True, 'output': output}}, result_ref=op, result_payload={'output': output})
        terminal = await obj.model_decision(replace(req, turn=2, operation_id=f'decision:{run}:2', transcript_version=version))
        assert terminal.decision_type == 'final'
        payload = owner.execute('SELECT result_payload FROM execution_operations WHERE operation_id=%s', (terminal.operation_id,)).fetchone()['result_payload']
        business = payload['response_receipt']['business_commands']
        assert business[0]['command_id'] == cid and business[0]['work_id'] == value['work']['work_id']
    assert owner.execute('SELECT count(*) n FROM works WHERE account_id=%s', (account_id,)).fetchone()['n'] == 1


async def test_forced_final_cannot_simulate_a_tool_or_finish_on_invalid_refs(urls, account_id):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        obj = runtime(store, [respond('done', [str(uuid4())])])
        with pytest.raises(ApplicationError, match='terminal action was rejected'):
            await obj.model_decision(model_input(run, account_id, segment, token, tid, final=True))


async def test_terminal_recovery_after_commit_does_not_repeat_model_or_command(urls, account_id, owner, monkeypatch):
    run = chat(urls, account_id)
    with execution(urls, account_id, run) as (store, segment, token):
        tid = context(store, run, account_id, segment)
        obj = runtime(store, [respond()])
        original = store.complete_operation_with_event
        def crash(**kw):
            version = original(**kw)
            if kw['operation_id'].endswith(':respond'):
                raise RuntimeError('lost reply after terminal commit')
            return version
        monkeypatch.setattr(store, 'complete_operation_with_event', crash)
        req = model_input(run, account_id, segment, token, tid)
        with pytest.raises(ApplicationError):
            await obj.model_decision(req)
        monkeypatch.setattr(store, 'complete_operation_with_event', original)
        result = await obj.model_decision(req)
        assert result.decision_type == 'final'
        assert obj.brain.generate_chat_decision.await_count == 1
    assert owner.execute("SELECT count(*) n FROM agent_transcript_events WHERE transcript_id=%s AND payload->'message'->>'name'='respond_directly'", (tid,)).fetchone()['n'] == 1
