"""Generic Work resumes persisted progress while chat actually executes on Temporal."""

import asyncio
import json
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from test_delegation_temporal import Actions, EventFactory, Resources

from agent_activities.runtime import DurableAgentActivities
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.react import ReactAgentWorkflow
from application.chat_execution import PostgresWebRequestLoader
from application.context_assembly import ContextAssemblyService
from application.context_builder import HarnessContextBuilder
from application.work_context import RunExecutionBindings, RunRequestLoader
from brain.contracts import BrainDecision
from conversation_domain.commands import CommandService
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_dispatcher import RunStrategyActivities
from orchestration.run_lifecycle_activities import RunLifecycleActivities
from orchestration.run_lifecycle_contracts import RunLifecycleInput
from orchestration.work_schedule import WorkScheduleService
from resources.run_budget import RunBudgetService
from run_domain.input import RunInputLoader
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from work_domain.models import Requirement

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]


async def test_generic_two_runs_restore_checkpoint_and_allow_concurrent_chat(
    commands, account_id, urls, owner, monkeypatch
):
    if not os.getenv('TEMPORAL_HOST'):
        pytest.skip('TEMPORAL_HOST required')
    queue = f'test-review-continuation-{uuid4()}'
    for module in ('agent_workflows.agent_run', 'agent_workflows.react',
                   'agent_workflows.segments', 'orchestration.agent_lifecycle_workflow'):
        monkeypatch.setattr(module + '.AGENT_TASK_QUEUE', queue)
    monkeypatch.setattr('orchestration.agent_lifecycle_workflow.WEB_LIFECYCLE_TASK_QUEUE', queue)
    work = commands.accept(account_id, str(uuid4()), 'Ongoing investigation', Requirement(
        'Save and continue evidence', 'generic_work', {'schema_version': 1}, completion_mode='ongoing'
    )).body['work']
    work_id = UUID(work['work_id'])
    initial_version = work['checkpoint']['checkpoint_version']
    run = commands.advance(account_id, work_id, str(uuid4()), work['row_version']).body['run']
    entered, release = asyncio.Event(), asyncio.Event()
    versions = []

    class Brain:
        async def rewrite_recall_query(self, *, user_content, **kwargs):
            return user_content, None

        async def generate_chat_decision(self, *, messages, tools=None):
            user = next(m['content'] for m in messages if m['role'] == 'user')
            if 'Chat while investigating' in user:
                return BrainDecision('Chat completed independently', [], 'stop', {})
            brief = json.loads(user)
            prior = brief['checkpoint']
            versions.append(prior['checkpoint_version'])
            if prior['checkpoint_version'] == initial_version:
                entered.set()
                await asyncio.wait_for(release.wait(), 20)
            else:
                assert prior['facts'] == ['verified first step']
                assert prior['source_run_id'] == run['run_id']
            version = prior['checkpoint_version'] + 1
            return BrainDecision(json.dumps({
                'schema_version': 1, 'kind': 'progress_saved', 'evidence': [],
                'continuation': {'schema_version': 1, 'kind': 'ready' if version == initial_version + 1 else 'awaiting_input',
                                 'reason': 'continue' if version == initial_version + 1 else 'needs_user_input'},
                'checkpoint': {'schema_version': 1, 'checkpoint_version': version,
                               'requirement_revision': brief['requirement_revision'],
                               'source_run_id': brief['input_ref'][4:],
                               'facts': ['verified first step'], 'decisions': [], 'unresolved': [],
                               'next_steps': [], 'evidence_refs': []},
            }), [], 'stop', {})

    agent_store = AgentDataStore(urls[2])
    lifecycle = WebRunLifecycleService(urls[2])
    runtime = DurableAgentActivities(
        store=agent_store, loader=RunRequestLoader(urls[2], PostgresWebRequestLoader(
            urls[2], ContextAssemblyService(urls[2], HarnessContextBuilder()))),
        brain=Brain(), actions=Actions(), event_factory=EventFactory(), resource_prep=Resources(),
        lifecycle=lifecycle, run_budget=RunBudgetService(urls[2]), context_bindings=RunExecutionBindings(),
    )
    controls = SegmentActivities(agent_store)
    lifecycle_activities = RunLifecycleActivities(lifecycle, RunInputLoader(agent_store))
    store = PostgresWorkflowExecutionStore(urls[2])
    client = await Client.connect(os.environ['TEMPORAL_HOST'], namespace=os.getenv('TEMPORAL_NAMESPACE', 'default'))
    histories = []

    async def execute(run_id):
        decision = await asyncio.to_thread(store.prepare_start, UUID(run_id))
        handle = await client.start_workflow(AgentLifecycleWorkflow.run, RunLifecycleInput(1, run_id),
                                              id=decision.workflow_id, task_queue=queue,
                                              execution_timeout=timedelta(seconds=60))
        result = await asyncio.wait_for(handle.result(), 65)
        histories.append(await handle.fetch_history())
        assert result['outcome'] == 'completed'

    async with Worker(client, task_queue=queue, workflow_runner=UnsandboxedWorkflowRunner(),
                      workflows=[AgentLifecycleWorkflow, AgentRunWorkflow, ReactAgentWorkflow],
                      activities=[lifecycle_activities.prepare_run, lifecycle_activities.finalize_failed,
                                  lifecycle_activities.finalize_cancelled, lifecycle_activities.load_agent_run_input,
                                  RunStrategyActivities(urls[2]).load_strategy, runtime.context_bootstrap,
                                  runtime.model_decision, runtime.finalize_agent_result, controls.acquire, controls.release]):
        first = asyncio.create_task(execute(run['run_id']))
        try:
            await asyncio.wait_for(entered.wait(), 20)
            chat = CommandService(urls[1])
            cid = UUID(chat.create_conversation(account_id, str(uuid4()))['conversation_id'])
            chat_run = chat.send_message(account_id, cid, str(uuid4()), 'Chat while investigating')['run_id']
            await execute(str(chat_run))
            assert not first.done()
            assert owner.execute('SELECT status FROM runs WHERE run_id=%s',
                                 (run['run_id'],)).fetchone()['status'] == 'running'
        finally:
            release.set()
            await first
        current = commands.get(account_id, work_id)['work']
        assert current['checkpoint']['checkpoint_version'] == initial_version + 1
        assert current['continuation']['kind'] == 'ready'
        scheduler = WorkScheduleService(urls[2])
        await asyncio.to_thread(scheduler.dispatch_due)
        following = commands.get(account_id, work_id)['work']['active_coordinator_run_id']
        assert following and following != run['run_id']
        await execute(following)
        await asyncio.to_thread(scheduler.dispatch_due)
        assert commands.get(account_id, work_id)['work']['active_coordinator_run_id'] is None
    current = commands.get(account_id, work_id)['work']
    assert versions == [initial_version, initial_version + 1]
    assert current['checkpoint']['checkpoint_version'] == initial_version + 2
    assert current['status'] == 'active' and current['continuation']['kind'] == 'awaiting_input'
    assert owner.execute('SELECT count(*) AS n FROM messages WHERE conversation_id=%s',
                         (cid,)).fetchone()['n'] == 2
    assert owner.execute('SELECT count(*) AS n FROM runs WHERE work_id=%s', (work_id,)).fetchone()['n'] == 2
    for history in histories:
        replay = await Replayer(workflows=[AgentLifecycleWorkflow],
                                workflow_runner=UnsandboxedWorkflowRunner()).replay_workflow(history)
        assert replay.replay_failure is None
