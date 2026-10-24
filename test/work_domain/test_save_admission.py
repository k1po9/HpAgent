"""Current save prerequisites gate Work admission, without model or external requests."""
import json
from dataclasses import replace
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from test_capability_facts import execution

from agent_activities.composition import compose_capabilities
from agent_activities.store import AgentDataStore
from agent_activities.text_result import TextActivities, text_definition
from agent_workflows.capability_contracts import CapabilityExecutionInput
from application.main_agent import create_main_work_tools
from file_runtime.output import OutputPublisher
from orchestration.work_schedule import WorkScheduleService
from persistence.uow import UnitOfWork
from resources.run_budget import RunBudgetService
from run_domain.acceptance import ResultAdoptionService
from run_domain.capability_contracts import AcceptancePredicate
from run_domain.execution import StaleFencingToken, assert_execution_fence
from run_domain.lifecycle import RunLifecycleService
from storage.tenant_file_store import TenantFileStore
from work_domain.commands import WorkConflict
from work_domain.models import Requirement
from work_domain.save_policy import InvalidWorkspaceSave
from workspace.catalog import WorkspaceCatalog
from workspace.file_scope import RunFileScope
from workspace.resources import ResourceDenied, ResourcePolicy

pytestmark = pytest.mark.postgres


def requirement(*, save=False, directory=None):
    criteria = [{'id': 'text', 'required': True, 'evidence_types': ['output_exists']}]
    if save:
        criteria.append({'id': 'save', 'required': True, 'evidence_types': ['workspace_saved']})
    return Requirement('Publish Markdown' + (' and save to Workspace' if save else ''),
        'generic_work', {'schema_version': 1}, acceptance_criteria=tuple(criteria),
        deliverable_policy={'schema_version': 1, 'required': bool(directory),
            **({'directory_id': str(directory)} if directory else {})})


@pytest.fixture(autouse=True)
def close_test_work(commands, account_id, urls, owner):
    yield
    for row in owner.execute("SELECT work_id FROM works WHERE account_id=%s AND status NOT IN ('completed','stopped')", (account_id,)).fetchall():
        wid = row['work_id']
        w = commands.get(account_id, wid)['work']
        if w['status'] in {'active', 'paused', 'pausing'}:
            commands.control(account_id, wid, str(uuid4()), w['row_version'], 'stop')
        for run in owner.execute("SELECT run_id FROM runs WHERE work_id=%s AND status IN ('queued','running','cancelling')", (wid,)).fetchall():
            RunLifecycleService(urls[2]).finish(account_id, run['run_id'], 'cancelled')


def grant(commands, account, wid, directory, operations=None):
    w = commands.get(account, wid)['work']
    return commands.integration(account, wid, str(uuid4()), w['row_version'], 'grant_resource', {
        'node_id': directory, 'operations': operations or ['create_child'], 'recursive': False,
    }).body['work']


def advance(commands, account, wid):
    w = commands.get(account, wid)['work']
    return commands.advance(account, wid, str(uuid4()), w['row_version']).body


def assert_no_run(owner, wid):
    assert owner.execute('SELECT count(*) AS n FROM runs WHERE work_id=%s', (wid,)).fetchone()['n'] == 0
    assert owner.execute("SELECT count(*) AS n FROM outbox_events WHERE work_id=%s AND event_type='start_run'", (wid,)).fetchone()['n'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('save,mixed', [(False, False), (False, True), (True, False), (True, True)])
async def test_publication_and_authorized_save_complete_with_exact_evidence(
    commands, account_id, urls, owner, tmp_path, save, mixed
):
    catalog = WorkspaceCatalog(urls[1])
    directory = UUID(catalog.initialize(account_id)['root_id']) if save else None
    req = requirement(save=save, directory=directory)
    if mixed:
        req = replace(req, acceptance_criteria=({'id': 'text', 'required': True,
            'evidence_types': ['output_exists', 'workspace_saved']},))
    w = commands.accept(account_id, 'accept', 'Markdown', req,
                         workspace_save_requested=save).body['work']
    wid = UUID(w['work_id'])
    if save:
        assert w['continuation']['reason'] == 'workspace_save_permission_required'
        assert_no_run(owner, wid)
        w = grant(commands, account_id, wid, directory)
        assert w['continuation']['kind'] == 'ready'
        WorkScheduleService(urls[2]).dispatch_due()
        run = owner.execute('SELECT run_id FROM runs WHERE work_id=%s', (wid,)).fetchone()['run_id']
    else:
        run = UUID(advance(commands, account_id, wid)['run']['run_id'])
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, run)
    caps = compose_capabilities(AgentDataStore(urls[2]), services=frozenset({'text'}))
    publisher = OutputPublisher(urls[2], TenantFileStore(tmp_path / 'files', max_bytes=1024 * 1024))
    text = TextActivities(caps, publisher)
    operation = f'publish:{run}'
    with execution(urls, account_id, run) as (store, _, _):
        store.begin_operation(operation, str(run), 'tool')
        with UnitOfWork(urls[2]) as uow:
            invocation = caps.facts.create(uow, operation_id=operation, capability_key='text', contract_version=1,
                inputs={'title': '中文成果', 'body': '持久化普通 Markdown，不需要模型或网络。'},
                scope={'schema_version': 1, 'node_ids': [], 'file_ids': [], 'tool_names': [], 'result_ids': []},
                invocation_limits=text_definition().limits)
    child = CapabilityExecutionInput(str(account_id), str(run), str(invocation['child_execution_id']),
        operation, 'TextCapabilityWorkflow', 60, 3, operation + ':produce')
    with execution(urls, account_id, run, child.execution_id) as (_, _, token):
        await text.produce(replace(child, lease_token=token))
    ref = owner.execute('SELECT ref_id,file_id FROM capability_result_refs WHERE invocation_id=%s', (operation,)).fetchone()
    if save:
        gid = ResourcePolicy(urls[1]).grants(account_id, 'work', wid)[0]['grant_id']
        ResourcePolicy(urls[1]).revoke(account_id, UUID(gid))
        with execution(urls, account_id, run), pytest.raises(ResourceDenied, match='create_child'):
            ResultAdoptionService(urls[2], caps.facts).save(str(ref['ref_id']),
                operation_id=f'{run}:denied', directory_id=str(directory), name='guide.md')
        # An explicit owner grant restores current authority; frozen admission alone did not.
        ResourcePolicy(urls[1]).grant(account_id, 'work', wid, directory, ['create_child'], False)
    with execution(urls, account_id, run):
        result = ResultAdoptionService(urls[2], caps.facts)
        evidence = [result.evidence('text', AcceptancePredicate('output_exists', str(ref['ref_id'])))]
        if save:
            saved = result.save(str(ref['ref_id']), operation_id=f'{run}:save', directory_id=str(directory), name='guide.md')
            evidence.append(result.evidence('text' if mixed else 'save', AcceptancePredicate(**saved['workspace_saved_predicate'])))
    lifecycle.finish(account_id, run, 'succeeded', accept_result=True, result={
        'schema_version': 1, 'kind': 'deliverable_ready', 'evidence': evidence,
        'continuation': {'schema_version': 1, 'kind': 'ready', 'reason': 'ready'},
    })
    final = commands.get(account_id, wid)['work']
    assert final['status'] == 'completed'
    assert len(final['workspace_saves']) == int(save)
    assert all(e['type'] != 'workspace_saved' for e in evidence) is (not save)
    file = owner.execute('SELECT storage_key FROM stored_files WHERE file_id=%s', (ref['file_id'],)).fetchone()
    with publisher.store.open(file['storage_key']) as stream:
        assert '中文成果' in stream.read().decode()


def test_unresolved_save_waits_then_revision_and_grant_release_only_current_requirement(commands, account_id, urls, owner):
    req = requirement(save=True)
    w = commands.accept(account_id, 'accept', 'Save later', req).body['work']
    wid = UUID(w['work_id'])
    assert w['continuation']['reason'] == 'workspace_save_destination_required'
    before = w['budget']
    for _ in range(2):
        WorkScheduleService(urls[2]).run_once()
        WorkScheduleService(urls[2]).dispatch_due()
        assert advance(commands, account_id, wid)['reason'] == 'workspace_save_destination_required'
    assert_no_run(owner, wid)
    assert commands.get(account_id, wid)['work']['budget'] == before
    directory = UUID(WorkspaceCatalog(urls[1]).initialize(account_id)['root_id'])
    w = commands.get(account_id, wid)['work']
    with pytest.raises(WorkConflict):
        commands.revise(account_id, wid, 'revise', w['row_version'] - 1, requirement(save=True, directory=directory))
    w = commands.revise(account_id, wid, 'revise', w['row_version'], requirement(save=True, directory=directory)).body['work']
    assert w['current_requirement_revision'] == 2
    assert w['continuation']['reason'] == 'workspace_save_permission_required'
    w = grant(commands, account_id, wid, directory, ['list_metadata'])
    assert w['continuation']['kind'] == 'awaiting_input'
    assert_no_run(owner, wid)
    grant(commands, account_id, wid, directory)
    WorkScheduleService(urls[2]).dispatch_due()
    row = owner.execute('SELECT requirement_revision FROM runs WHERE work_id=%s', (wid,)).fetchone()
    assert row['requirement_revision'] == 2
    old = owner.execute('SELECT deliverable_policy,acceptance_criteria FROM work_requirements WHERE work_id=%s AND revision=1', (wid,)).fetchone()
    assert old['deliverable_policy'] == req.to_dict()['deliverable_policy']
    assert old['acceptance_criteria'] == list(req.acceptance_criteria)


@pytest.mark.parametrize('destination', ['missing', 'foreign', 'deleted'])
def test_unavailable_directories_never_admit(commands, account_id, urls, owner, destination):
    directory = uuid4()
    if destination != 'missing':
        target_account = uuid4() if destination == 'foreign' else account_id
        if target_account != account_id:
            owner.execute('INSERT INTO accounts(account_id) VALUES (%s)', (target_account,))
        root = UUID(WorkspaceCatalog(urls[1]).initialize(target_account)['root_id'])
        directory = WorkspaceCatalog(urls[1]).create_directory(target_account, root, 'Target')
        if destination == 'deleted':
            owner.execute('UPDATE workspace_nodes SET deleted_at=now() WHERE node_id=%s', (directory,))
    w = commands.accept(account_id, str(uuid4()), 'Save', requirement(save=True, directory=directory)).body['work']
    wid = UUID(w['work_id'])
    assert w['continuation']['reason'] == 'workspace_save_destination_unavailable'
    assert advance(commands, account_id, wid)['reason'] == 'workspace_save_destination_unavailable'
    assert_no_run(owner, wid)


def test_revoked_grant_is_rechecked_at_admission(commands, account_id, urls, owner):
    root = UUID(WorkspaceCatalog(urls[1]).initialize(account_id)['root_id'])
    w = commands.accept(account_id, 'accept', 'Save', requirement(save=True, directory=root)).body['work']
    wid = UUID(w['work_id'])
    grant(commands, account_id, wid, root)
    gid = ResourcePolicy(urls[1]).grants(account_id, 'work', wid)[0]['grant_id']
    ResourcePolicy(urls[1]).revoke(account_id, UUID(gid))
    assert advance(commands, account_id, wid)['reason'] == 'workspace_save_permission_required'
    assert_no_run(owner, wid)


@pytest.mark.parametrize('versioned', [False, True])
def test_exact_existing_entry_requires_a_versioned_target_and_update_grant(commands, account_id, urls, owner, tmp_path, versioned):
    catalog = WorkspaceCatalog(urls[1])
    root = UUID(catalog.initialize(account_id)['root_id'])
    seed = commands.accept(account_id, 'seed', 'Seed bytes', requirement()).body['work']
    seed_run = UUID(advance(commands, account_id, UUID(seed['work_id']))['run']['run_id'])
    for name in ('inputs', 'scratch', 'outputs'):
        (tmp_path / name).mkdir()
    scope = RunFileScope(seed_run, tmp_path / 'inputs', tmp_path / 'scratch', tmp_path / 'outputs', ())
    (scope.outputs_root / 'seed.md').write_text('original', encoding='utf-8')
    publisher = OutputPublisher(urls[2], TenantFileStore(tmp_path / 'store', max_bytes=1024 * 1024))
    file = publisher.publish(scope, 'seed-publish', 'seed.md', 'text/markdown', encoding='utf-8').file_id
    node = catalog.save_file(account_id, root, file, 'existing.md', 'seed-save')
    RunLifecycleService(urls[2]).finish(account_id, seed_run, 'failed', failure_code='fixture_seed')
    if versioned:
        catalog.upgrade_file(account_id, node)
    value = requirement(save=True).to_dict()
    value['deliverable_policy']['entry_id'] = str(node)
    w = commands.accept(account_id, 'update', 'Save existing', Requirement.from_dict(value)).body['work']
    wid = UUID(w['work_id'])
    assert w['continuation']['reason'] == (
        'workspace_save_permission_required' if versioned else 'workspace_save_destination_unavailable')
    # create_child does not authorize updating an existing file.
    assert grant(commands, account_id, wid, root)['continuation']['kind'] == 'awaiting_input'
    w = grant(commands, account_id, wid, node, ['update_content'])
    assert w['continuation']['kind'] == ('ready' if versioned else 'awaiting_input')
    if versioned:
        assert advance(commands, account_id, wid)['run']['requirement_revision'] == 1
    else:
        assert_no_run(owner, wid)


@pytest.mark.asyncio
async def test_main_rejects_unrequested_save_atomically_but_accepts_explicit_pending_save(commands, account_id, owner):
    from conversation_domain.commands import CommandService

    chat = CommandService(commands.database)
    cid = UUID(chat.create_conversation(account_id, 'chat').body['conversation_id'])
    sent = chat.send_message(account_id, cid, 'message', 'Generate downloadable Markdown')
    mid = owner.execute('SELECT trigger_message_id FROM runs WHERE run_id=%s', (sent['run_id'],)).fetchone()['trigger_message_id']
    tool = next(t for t in create_main_work_tools({'account_id': str(account_id), 'conversation_id': str(cid),
        'trigger_message_id': str(mid)}, commands) if t.name == 'accept_work')
    with pytest.raises(InvalidWorkspaceSave, match='Ordinary published/downloadable'):
        await tool.ainvoke({'title': 'Bad save', 'mandate_slot': 'goal', 'requirement': requirement(save=True).to_dict()})
    assert commands.list(account_id)['items'] == []
    accepted = json.loads(await tool.ainvoke({'title': 'Text', 'mandate_slot': 'goal', 'requirement': requirement().to_dict()}))
    assert accepted['work']['continuation']['kind'] == 'ready'
    pending = json.loads(await tool.ainvoke({'title': 'Explicit save', 'mandate_slot': 'save',
        'requirement': requirement(save=True).to_dict(), 'workspace_save_requested': True}))
    assert pending['work']['continuation']['reason'] == 'workspace_save_destination_required'


def test_legacy_zero_target_replay_and_explicit_revision_preserve_budget(commands, account_id, urls, owner):
    old = requirement(save=True).to_dict()
    old['deliverable_policy']['directory_id'] = '00000000-0000-0000-0000-000000000000'
    req = Requirement.from_dict(old)
    # Emulate the historical writer; no immutable requirement is updated.
    with patch('work_domain.commands.validate_new_save_policy'), patch('work_domain.commands.defer_workspace_save'):
        receipt = commands.accept(account_id, 'old', 'Legacy', req)
    wid = UUID(receipt.body['work']['work_id'])
    assert commands.accept(account_id, 'old', 'Legacy', req).replayed
    assert advance(commands, account_id, wid)['reason'] == 'workspace_save_destination_required'
    assert_no_run(owner, wid)
    before = commands.get(account_id, wid)['work']
    w = commands.revise(account_id, wid, 'fix', before['row_version'], requirement(save=True)).body['work']
    assert w['budget'] == before['budget']
    assert w['current_requirement_revision'] == 2 and w['continuation']['kind'] == 'awaiting_input'
    assert owner.execute('SELECT deliverable_policy FROM work_requirements WHERE work_id=%s AND revision=1', (wid,)).fetchone()['deliverable_policy'] == old['deliverable_policy']
    with pytest.raises(InvalidWorkspaceSave):
        commands.accept(account_id, 'new-bad', 'Invalid', req)


def test_revision_fences_old_save_and_resume_keeps_missing_permission_wait(commands, account_id, urls, owner):
    root = UUID(WorkspaceCatalog(urls[1]).initialize(account_id)['root_id'])
    w = commands.accept(account_id, 'accept', 'Save', requirement(save=True, directory=root)).body['work']
    wid = UUID(w['work_id'])
    grant(commands, account_id, wid, root)
    run = UUID(advance(commands, account_id, wid)['run']['run_id'])
    RunLifecycleService(urls[2]).start(account_id, run)
    budget = RunBudgetService(urls[2])
    budget.reserve(run, 'used', {'tool_calls': 1})
    budget.settle(run, 'used', {'tool_calls': 1}, 'measured')
    from run_domain.execution_context import fence_scope
    lease = AgentDataStore(urls[2]).acquire_lease(str(account_id), str(run))
    old_fence = (str(account_id), str(run), lease.fencing_token, lease.execution_id)
    with fence_scope(*old_fence), UnitOfWork(urls[2]) as uow:
        assert_execution_fence(uow)
    w = commands.get(account_id, wid)['work']
    w = commands.revise(account_id, wid, 'revise', w['row_version'], requirement(save=True)).body['work']
    assert w['continuation']['kind'] == 'awaiting_input'
    with fence_scope(*old_fence), pytest.raises(StaleFencingToken):
        WorkspaceCatalog(urls[2]).save_file(account_id, root, uuid4(), 'stale.md', 'old-save', agent_run_id=run)
    RunLifecycleService(urls[2]).finish(account_id, run, 'cancelled')
    w = commands.get(account_id, wid)['work']
    w = commands.control(account_id, wid, 'pause', w['row_version'], 'pause').body['work']
    w = commands.control(account_id, wid, 'resume', w['row_version'], 'resume').body['work']
    assert w['continuation']['reason'] == 'workspace_save_destination_required'
    assert w['budget']['used']['tool_calls'] == 1
    assert len(owner.execute('SELECT run_id FROM runs WHERE work_id=%s', (wid,)).fetchall()) == 1


@pytest.mark.parametrize('gate', ['paused', 'budget', 'uncertain', 'other_input'])
def test_grant_does_not_release_unrelated_control_gates(commands, account_id, urls, owner, gate):
    root = UUID(WorkspaceCatalog(urls[1]).initialize(account_id)['root_id'])
    w = commands.accept(account_id, 'accept', 'Save', requirement(save=True, directory=root)).body['work']
    wid = UUID(w['work_id'])
    if gate == 'paused':
        commands.control(account_id, wid, 'pause', w['row_version'], 'pause')
    else:
        next_step = {
            'budget': {'schema_version': 1, 'kind': 'blocked', 'reason': 'budget_exhausted'},
            'uncertain': {**w['continuation'], 'operation_ref': 'unknown-effect'},
            'other_input': {'schema_version': 1, 'kind': 'awaiting_input', 'reason': 'user_acceptance_required'},
        }[gate]
        owner.execute('UPDATE works SET continuation=%s::jsonb,row_version=row_version+1,updated_at=now() WHERE work_id=%s',
                      (json.dumps(next_step), wid))
    before = commands.get(account_id, wid)['work']
    current = grant(commands, account_id, wid, root)
    assert current['status'] == before['status']
    assert current['continuation'] == before['continuation']
    assert current['budget'] == before['budget']
    WorkScheduleService(urls[2]).dispatch_due()
    assert_no_run(owner, wid)
