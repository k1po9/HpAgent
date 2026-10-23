"""Main command -> Temporal -> trusted web tools -> persisted reply; no real model/network."""
import asyncio
import json
import os
from contextlib import AsyncExitStack
from uuid import UUID, uuid4

import httpx
import pytest
from support.lifecycle_workers import lifecycle_control_activities
from temporalio.client import Client
from temporalio.worker import Replayer

from actions.contracts import ActionRequest
from agent_activities.runtime import DurableAgentActivities
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_activities.web_tools import WebTools
from application.chat_execution import PostgresWebRequestLoader
from application.context_assembly import ContextAssemblyService
from application.context_builder import HarnessContextBuilder
from brain.contracts import BrainDecision
from conversation_domain.commands import CommandService
from conversation_domain.execution_bindings import ChatExecutionBindings
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_lifecycle_activities import RunLifecycleActivities
from orchestration.web_dispatcher import (
    TemporalClientAdapter,
    TemporalOutboxDispatcher,
    WebOutboxDispatcher,
)
from orchestration.web_workers import build_web_temporal_workers
from research_adapters.deepseek import DeepSeekWebProvider
from research_adapters.discovery import CompositeSourceDiscoveryProvider
from research_domain.models import SourceCandidate
from resources.run_budget import RunBudgetService
from run_domain.input import RunInputLoader
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.outbox import OutboxService
from web_domain.run_events import RedisWebRunEventSinkFactory
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from web_retrieval.budget import paid_before_request
from web_retrieval.safety import SafeLocalFetcher
from web_retrieval.service import WebRetrievalService
from workspace.execution import ExecutionResourceService

from .test_web_temporal_e2e import Actions, Sandbox

pytestmark = [pytest.mark.asyncio, pytest.mark.postgres, pytest.mark.temporal]


@pytest.mark.parametrize('provider', ['legacy', 'deepseek'])
async def test_main_lightweight_web_keeps_one_chat_run_without_research(
        db, account_id, database_url, worker_database_url, tmp_path, provider):
    url = 'https://docs.python.org/3/library/asyncio-task.html'
    sent, decisions = [], []

    def cloud_response(request):
        sent.append(request.url.path)
        if request.url.path.endswith('search'):
            return httpx.Response(200, json={'results': [{'url': url, 'title': 'Tasks', 'snippet': 'TaskGroup'}]})
        return httpx.Response(200, json={'title': 'Tasks', 'content': '# TaskGroup\n\nOfficial example text.'})

    class Search:
        async def discover(self, *args, **kwargs):
            sent.append('legacy-search')
            return [SourceCandidate(url, 'Tasks', 'TaskGroup')]

    class Brain:
        async def rewrite_recall_query(self, **kwargs):
            return 'TaskGroup', None

        async def generate_chat_decision(self, *, messages, tools=None):
            names = {tool['function']['name'] for tool in tools}
            assert {'web_search', 'web_fetch'} <= names
            assert not any(name.startswith('capability__research') for name in names)
            observations = [json.loads(m['content']) for m in messages if m['role'] == 'tool']
            decisions.append(len(observations))
            if not observations:
                return BrainDecision('', [ActionRequest('search-1', 'web_search', {'query': 'asyncio TaskGroup'})], 'tool_calls', {})
            if len(observations) == 1:
                assert observations[0]['sources'][0]['url'] == url
                return BrainDecision('', [ActionRequest('fetch-1', 'web_fetch', {'url': url})], 'tool_calls', {})
            assert observations[1]['requested_url'] == url and observations[1]['text']
            assert observations[1]['final_url_verified'] is (provider == 'legacy')
            return BrainDecision('Source: ' + url, [], 'stop', {})

    async def resolver(host, port):
        return '93.184.216.34'

    def local_response(request):
        sent.append('local-fetch')
        return httpx.Response(200, text='TaskGroup official source body.')

    local = SafeLocalFetcher(resolver=resolver, transport=httpx.MockTransport(local_response),
                            extractor=lambda html, _: html)
    command = CommandService(database_url, budget_mode='observe')
    conversation = UUID(command.create_conversation(account_id, str(uuid4()))['conversation_id'])
    run_id = UUID(command.send_message(account_id, conversation, str(uuid4()), 'Find TaskGroup docs')['run_id'])
    client = await Client.connect(os.environ['TEMPORAL_HOST'], namespace=os.environ['TEMPORAL_NAMESPACE'])
    store = AgentDataStore(worker_database_url)
    lifecycle = WebRunLifecycleService(worker_database_url)
    events = RedisWebRunEventSinkFactory(None)
    budget = RunBudgetService(worker_database_url)
    async with httpx.AsyncClient(transport=httpx.MockTransport(cloud_response)) as cloud_client:
        cloud = DeepSeekWebProvider('test-only', client=cloud_client, before_request=paid_before_request)
        service = WebRetrievalService({'legacy': Search(), 'deepseek': CompositeSourceDiscoveryProvider([cloud])},
            {'local': local, 'deepseek': cloud}, default_search=provider,
            default_fetch='local' if provider == 'legacy' else 'deepseek')
        runtime = DurableAgentActivities(store=store, loader=PostgresWebRequestLoader(worker_database_url,
            ContextAssemblyService(worker_database_url, HarnessContextBuilder())), brain=Brain(),
            actions=Actions(), event_factory=events, lifecycle=lifecycle, run_budget=budget,
            context_bindings=ChatExecutionBindings(), web_tools=WebTools(service, budget),
            resource_prep=ExecutionResourceService(worker_database_url, Sandbox(), execution_root=tmp_path/'executions'))
        controls = SegmentActivities(store)
        lifecycle_activities = RunLifecycleActivities(lifecycle, RunInputLoader(store), events)
        workers = build_web_temporal_workers(client,
            lifecycle_activities=[*lifecycle_control_activities(worker_database_url, lifecycle_activities), runtime.finalize_agent_result],
            agent_activities=[controls.acquire, controls.release, controls.begin_wait, controls.finish_wait,
                              runtime.context_bootstrap, runtime.model_decision, runtime.tool_execution])
        adapter = TemporalClientAdapter(client)
        outbox = WebOutboxDispatcher(OutboxService(worker_database_url), TemporalOutboxDispatcher(
            PostgresWorkflowExecutionStore(worker_database_url), adapter, lifecycle), 'main-retrieval-test')
        async with AsyncExitStack() as stack:
            await stack.enter_async_context(workers.lifecycle)
            await stack.enter_async_context(workers.agent)
            await outbox.run_once()
            handle = client.get_workflow_handle(f'hpagent-web-run-{run_id}')
            assert (await asyncio.wait_for(handle.result(), 60))['outcome'] == 'completed'
            history = await handle.fetch_history()
            await outbox.run_once()
        await Replayer(workflows=[AgentLifecycleWorkflow]).replay_workflow(history)
    assert len(sent) == 2 and decisions == [0, 1, 2]
    assert db.execute('SELECT used FROM run_budgets WHERE run_id=%s', (run_id,)).fetchone()[0]['tool_calls'] == 2
    assert db.execute('SELECT count(*) FROM works WHERE account_id=%s', (account_id,)).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM capability_invocations WHERE account_id=%s', (account_id,)).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM research_plans WHERE run_id=%s', (run_id,)).fetchone()[0] == 0
    assert db.execute('SELECT count(*) FROM runs WHERE account_id=%s', (account_id,)).fetchone()[0] == 1
