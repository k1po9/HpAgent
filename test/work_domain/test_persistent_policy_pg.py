"""Policy freezing uses the normal three-role transaction boundaries."""
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest

from agent_activities.store import AgentDataStore
from common.agent_policy import AgentPolicy
from conversation_domain.commands import CommandService
from orchestration.work_schedule import WorkScheduleService
from run_domain.input import RunInputLoader
from work_domain.commands import WorkCommandService
from work_domain.models import Requirement

pytestmark = pytest.mark.postgres


def test_chat_admission_replay_and_explicit_retry_freeze_new_policy(urls, account_id, owner):
    first = CommandService(urls[1], agent_policy=AgentPolicy(max_tool_turns=7))
    cid = UUID(first.create_conversation(account_id, str(uuid4()))['conversation_id'])
    key = str(uuid4())
    sent = first.send_message(account_id, cid, key, 'hello')
    run = UUID(sent['run_id'])
    second = CommandService(urls[1], agent_policy=AgentPolicy(max_tool_turns=11))
    assert second.send_message(account_id, cid, key, 'hello')['run_id'] == str(run)
    loader = RunInputLoader(AgentDataStore(urls[2]), max_turns=99)
    assert loader.load(str(run)).max_turns == 7
    assert owner.execute("SELECT count(*) AS n FROM outbox_events WHERE run_id=%s AND event_type='start_run'", (run,)).fetchone()['n'] == 1
    with pytest.raises(psycopg.Error, match='immutable'):
        owner.execute("UPDATE runs SET input_snapshot='{}'::jsonb WHERE run_id=%s", (run,))
    first.fail_run(account_id, run, 'test_failure')
    retry_key = str(uuid4())
    retried = second.retry_run(account_id, run, retry_key)
    assert loader.load(retried['run_id']).max_turns == 11
    assert first.retry_run(account_id, run, retry_key)['run_id'] == retried['run_id']


@pytest.mark.parametrize('scheduled', [False, True])
def test_work_command_and_due_admission_freeze_policy(urls, account_id, owner, scheduled):
    policy = AgentPolicy(max_tool_turns=8, default_timezone='UTC')
    commands = WorkCommandService(urls[1], agent_policy=policy)
    due = datetime.now(UTC) + timedelta(minutes=2)
    timing = {'schema_version': 1, 'kind': 'once', 'timezone': 'UTC', 'due_at': due.isoformat()} if scheduled else None
    req = Requirement('investigate policy', 'generic_work', {'schema_version': 1, 'reasoning_mode': 'react'}, timing=timing)
    work = commands.accept(account_id, str(uuid4()), 'policy', req).body['work']
    wid = UUID(work['work_id'])
    if scheduled:
        WorkScheduleService(urls[2], agent_policy=policy).run_once(now=due + timedelta(seconds=1))
        run = commands.get(account_id, wid)['work']['active_coordinator_run_id']
    else:
        run = commands.advance(account_id, wid, str(uuid4()), work['row_version']).body['run']['run_id']
    assert RunInputLoader(AgentDataStore(urls[2]), max_turns=77).load(str(run)).max_turns == 8
    snap = owner.execute('SELECT input_snapshot FROM runs WHERE run_id=%s', (UUID(run),)).fetchone()['input_snapshot']
    assert snap['agent_policy']['default_timezone'] == 'UTC'


@pytest.mark.asyncio
async def test_research_stages_use_frozen_thresholds_after_worker_change(urls, account_id, owner, tmp_path):
    from test_capability_facts import chat
    from test_research_capability import admit, produce, setup

    from research_domain.capability import research_definition
    from research_domain.policy import ResearchPolicy
    from run_domain.capability_contracts import CapabilityRegistry
    research, caps = setup(urls, tmp_path)
    policy = ResearchPolicy(synthesis_prompt='frozen', max_sources=1, max_fetches=1,
                            min_evidence=1, min_distinct_sources=1)
    caps.facts.registry = CapabilityRegistry((research_definition(policy),))
    run = chat(urls, account_id)
    request = admit(caps, urls, account_id, run, 'frozen')
    research.max_sources = 0
    research.max_fetches = 0
    research.min_evidence = 99
    research.min_distinct_sources = 99
    await produce(research, request)
    assert owner.execute('SELECT count(*) AS n FROM source_records WHERE invocation_id=%s',
                         (request.invocation_id,)).fetchone()['n'] == 1
    report = owner.execute('SELECT report_markdown FROM research_reports WHERE invocation_id=%s',
                           (request.invocation_id,)).fetchone()['report_markdown']
    assert 'A finding' in report


def test_research_parent_recovery_keeps_original_definition_after_config_change(urls, account_id, owner, tmp_path):
    from test_capability_facts import execution
    from test_research_capability import setup

    from orchestration.research_workflow import ResearchWorkflowInput
    from research_activities.composition import compose_research
    from research_domain.capability import research_definition
    from research_domain.policy import ResearchPolicy
    from run_domain.capability_contracts import CapabilityRegistry
    from run_domain.lifecycle import RunLifecycleService
    commands = WorkCommandService(urls[1])
    work = commands.accept(account_id, str(uuid4()), 'research',
        Requirement('Find evidence', 'research_report', {'schema_version': 1, 'source_strategy': {}})).body['work']
    run = UUID(commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version']).body['run']['run_id'])
    RunLifecycleService(urls[2]).start(account_id, run)
    research, _ = setup(urls, tmp_path)
    caps, parent = compose_research(research)
    assert caps.facts.registry.get('research', 1)['implementation_version'] == 'research-web-v3'
    request = ResearchWorkflowInput(1, str(run))
    with execution(urls, account_id, run):
        first = parent._admit(request)
    original = owner.execute('SELECT descriptor FROM capability_invocations WHERE invocation_id=%s', (first.invocation_id,)).fetchone()['descriptor']
    caps.facts.registry = CapabilityRegistry((research_definition(ResearchPolicy(synthesis_prompt='changed', max_sources=9)),))
    with execution(urls, account_id, run):
        assert parent._admit(request) == first
    assert owner.execute('SELECT descriptor FROM capability_invocations WHERE invocation_id=%s', (first.invocation_id,)).fetchone()['descriptor'] == original


def test_large_new_policy_preserves_previous_input_bound_and_immutable_descriptor(urls, account_id, owner, tmp_path):
    from test_capability_contracts import scope
    from test_capability_facts import chat, execution
    from test_research_capability import setup

    from persistence.uow import UnitOfWork
    from research_domain.capability import research_definition
    from research_domain.policy import ResearchPolicy
    from run_domain.capability_contracts import CapabilityRegistry
    research, caps = setup(urls, tmp_path)
    policy = ResearchPolicy(synthesis_prompt='p' * 16000)
    definition = research_definition(policy)
    caps.facts.registry = CapabilityRegistry((definition,))
    run = chat(urls, account_id)
    operation = f'{run}:large'
    with execution(urls, account_id, run) as (store, _, _):
        store.begin_operation(operation, str(run), 'tool')
        with UnitOfWork(urls[2]) as uow:
            invocation = caps.facts.create(uow, operation_id=operation, capability_key='research',
                contract_version=1, inputs={'objective': 'x' * 20000}, scope=scope(),
                invocation_limits=definition.limits)
    assert len(invocation['descriptor']['inputs']['objective']) == 20000
    assert invocation['descriptor']['definition']['execution_policy']['synthesis_prompt'] == 'p' * 16000
    with pytest.raises(psycopg.Error, match='immutable'):
        owner.execute("UPDATE capability_invocations SET descriptor='{}'::jsonb WHERE invocation_id=%s", (operation,))


@pytest.mark.asyncio
async def test_frozen_research_policy_does_not_preserve_revoked_account_access(urls, account_id, owner, tmp_path):
    from temporalio.exceptions import ApplicationError
    from test_capability_facts import chat
    from test_research_capability import admit, setup

    from orchestration.research_workflow import ResearchIterationInput
    from research_domain.capability import research_definition
    from research_domain.policy import ResearchPolicy
    from run_domain.capability_contracts import CapabilityRegistry
    from run_domain.execution import RunNotExecutable, StaleFencingToken
    research, caps = setup(urls, tmp_path)
    caps.facts.registry = CapabilityRegistry((research_definition(ResearchPolicy(synthesis_prompt='frozen')),))
    run = chat(urls, account_id)
    request = admit(caps, urls, account_id, run, 'revoked')
    await research.prepare_research_activity(request)
    await research.create_research_plan_activity(request)
    owner.execute("UPDATE accounts SET status='disabled',version=version+1,updated_at=now() WHERE account_id=%s", (account_id,))
    with pytest.raises((RunNotExecutable, StaleFencingToken, ApplicationError)):
        await research.discover_research_sources_activity(ResearchIterationInput(
            1, request.run_id, 1, request.invocation_id, request.account_id, request.execution_id))
    assert owner.execute('SELECT count(*) AS n FROM source_records WHERE invocation_id=%s',
                         (request.invocation_id,)).fetchone()['n'] == 0
