"""Text-specific publication recovery and current Work authority, no real model."""

from dataclasses import replace
from uuid import UUID

import pytest
from test_capability_facts import execution

from agent_activities.composition import compose_capabilities
from agent_activities.store import AgentDataStore
from agent_activities.text_result import TextActivities, text_definition
from agent_workflows.capability_contracts import CapabilityExecutionInput
from file_runtime.output import OutputPublisher
from persistence.uow import UnitOfWork
from run_domain.lifecycle import RunLifecycleService
from storage.tenant_file_store import TenantFileStore
from work_domain.models import Requirement

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


async def test_text_file_commit_loss_replays_bytes_and_paused_child_is_fenced(commands, account_id, urls, owner, tmp_path, monkeypatch):
    work = commands.accept(account_id, 'publish-replay', 'Text', Requirement(
        'Publish text', 'generic_work', {'schema_version': 1},
        acceptance_criteria=({'id': 'text', 'required': True, 'evidence_types': ['output_exists']},),
    )).body['work']
    wid = UUID(work['work_id'])
    run = UUID(commands.advance(account_id, wid, 'advance', work['row_version']).body['run']['run_id'])
    RunLifecycleService(urls[2]).start(account_id, run)
    caps = compose_capabilities(AgentDataStore(urls[2]), services=frozenset({'text'}))
    text = TextActivities(caps, OutputPublisher(urls[2], TenantFileStore(tmp_path / 'files', max_bytes=1024 * 1024)))
    operation = f'publish:{run}'
    with execution(urls, account_id, run) as (store, _, _):
        store.begin_operation(operation, str(run), 'tool')
        with UnitOfWork(urls[2]) as uow:
            invocation = caps.facts.create(uow, operation_id=operation, capability_key='text', contract_version=1,
                inputs={'title': '结果', 'body': '精确不可变正文'},
                scope={'schema_version': 1, 'node_ids': [], 'file_ids': [], 'tool_names': [], 'result_ids': []},
                invocation_limits=text_definition().limits)
    child = CapabilityExecutionInput(str(account_id), str(run), str(invocation['child_execution_id']),
        operation, 'TextCapabilityWorkflow', 60, 3, operation + ':produce')
    committed = caps.commit_result
    def lost(*args, **kwargs):
        raise RuntimeError('response lost after file publication')
    with execution(urls, account_id, run, child.execution_id) as (_, _, token):
        current = replace(child, lease_token=token)
        monkeypatch.setattr(caps, 'commit_result', lost)
        with pytest.raises(RuntimeError, match='response lost'):
            await text.produce(current)
        assert owner.execute('SELECT count(*) n FROM output_publish_operations WHERE run_id=%s', (run,)).fetchone()['n'] == 1
        assert owner.execute('SELECT count(*) n FROM capability_results WHERE run_id=%s', (run,)).fetchone()['n'] == 0
        monkeypatch.setattr(caps, 'commit_result', committed)
        result = await text.produce(current)
        assert await text.produce(current) == result
        assert owner.execute('SELECT count(*) n FROM output_publish_operations WHERE run_id=%s', (run,)).fetchone()['n'] == 1
        assert owner.execute('SELECT count(*) n FROM capability_results WHERE run_id=%s', (run,)).fetchone()['n'] == 1
    # A user command has no Agent execution fence; the stale producer retains its old token.
    latest = commands.get(account_id, wid)['work']
    commands.control(account_id, wid, 'pause', latest['row_version'], 'pause')
    with pytest.raises(Exception) as denied:
        await text.produce(current)
    assert getattr(denied.value, 'code', None) == 'stale_fencing_token' or 'stale' in str(denied.value).lower()
