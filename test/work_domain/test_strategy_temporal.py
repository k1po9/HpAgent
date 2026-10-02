"""Small real Temporal check for the unified finite strategy entrypoint."""
import os
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import UnsandboxedWorkflowRunner, Worker

from agent_activities.store import AgentDataStore
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_dispatcher import ReminderActivities, RunStrategyActivities
from orchestration.run_lifecycle_activities import RunLifecycleActivities
from orchestration.run_lifecycle_contracts import RunLifecycleInput
from run_domain.input import RunInputLoader
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore
from work_domain.models import Requirement

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]


async def test_unified_temporal_entry_runs_reminder_without_agent_worker(commands, account_id, urls, owner, monkeypatch):
    host = os.getenv('TEMPORAL_HOST')
    if not host:
        pytest.skip('TEMPORAL_HOST required')
    queue = f'test-durable-work-strategy-{uuid4()}'
    # Queue constants are deployment addresses; isolate this small test from live workers.
    monkeypatch.setattr('orchestration.agent_lifecycle_workflow.WEB_LIFECYCLE_TASK_QUEUE', queue)
    client = await Client.connect(host, namespace=os.getenv('TEMPORAL_NAMESPACE', 'default'))
    accepted = commands.accept(account_id, str(uuid4()), 'Reminder', Requirement(
        'Remember meeting', 'reminder', {'schema_version': 1, 'content': 'Meeting'},
    ))
    work_id = UUID(accepted.body['work']['work_id'])
    run = commands.advance(account_id, work_id, str(uuid4()), accepted.body['work']['row_version']).body['run']
    run_id = UUID(run['run_id'])
    decision = PostgresWorkflowExecutionStore(urls[2]).prepare_start(run_id)
    lifecycle = RunLifecycleActivities(WebRunLifecycleService(urls[2]), RunInputLoader(AgentDataStore(urls[2])))
    async with Worker(client, task_queue=queue, workflow_runner=UnsandboxedWorkflowRunner(), workflows=[AgentLifecycleWorkflow], activities=[
        lifecycle.prepare_run, lifecycle.finalize_cancelled, lifecycle.finalize_failed,
        RunStrategyActivities(urls[2]).load_strategy, ReminderActivities(urls[2]).execute,
    ]):
        result = await client.execute_workflow(
            AgentLifecycleWorkflow.run, RunLifecycleInput(1, str(run_id)), id=decision.workflow_id,
            task_queue=queue, execution_timeout=timedelta(seconds=45),
        )
    assert result['outcome'] == 'completed'
    row = owner.execute('SELECT status,result_json FROM runs WHERE run_id=%s', (run_id,)).fetchone()
    assert row['status'] == 'succeeded' and row['result_json']['kind'] == 'notification_enqueued'
    assert commands.get(account_id, work_id)['work']['continuation']['kind'] == 'awaiting_delivery'
