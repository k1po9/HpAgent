"""Real publication, parent adoption, completion and recovery on isolated PG."""
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from test_capability_facts import execution
from test_capability_harness import call, finish
from test_delivery_contract_unit import requirement
from test_output_evidence import FAKE_CALL, finalize, publish

from agent_activities.composition import compose_capabilities
from agent_activities.store import AgentDataStore
from agent_activities.text_result import TextActivities
from file_runtime.output import OutputPublisher
from persistence.uow import UnitOfWork
from run_domain.lifecycle import RunLifecycleService
from storage.tenant_file_store import TenantFileStore
from work_domain.deliverables import validate_delivery_evidence

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def setup(commands, account_id, urls, formats=('markdown',), extra=()):
    req = requirement(formats, extra)
    work = commands.accept(account_id, str(uuid4()), 'Formats', req).body['work']
    wid = UUID(work['work_id'])
    run = UUID(commands.advance(account_id, wid, str(uuid4()), work['row_version']).body['run']['run_id'])
    RunLifecycleService(urls[2]).start(account_id, run)
    return wid, run, req


def evidence(owner, run):
    return owner.execute('SELECT * FROM capability_acceptance_evidence WHERE run_id=%s ORDER BY created_at', (run,)).fetchall()


@pytest.mark.parametrize('formats', [('markdown',), ('docx',), ('markdown', 'docx')])
async def test_publishing_without_evidence_tool_completes_exact_targets(commands, account_id, urls, owner, tmp_path, formats):
    wid, run, _ = setup(commands, account_id, urls, formats)
    for i, format in enumerate(formats):
        caps, tool = await publish(urls, account_id, run, wid, tmp_path, format, i)
        with execution(urls, account_id, run) as (_, _, token):
            await caps.observe(finish(tool, token))
    rows = evidence(owner, run)
    assert len(rows) == len(formats) * 2
    for i, _ in enumerate(formats):
        pair = [r for r in rows if r['criterion_id'] in {f'output{i}', f'format{i}'}]
        assert len({r['predicate']['subject_ref'] for r in pair}) == 1
    finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['status'] == 'completed'
    assert owner.execute("SELECT count(*) n FROM notifications WHERE work_id=%s AND payload->>'event_type'='completed'", (wid,)).fetchone()['n'] == 1


async def test_repeated_publications_cannot_mix_file_evidence(commands, account_id, urls, owner, tmp_path):
    wid, run, req = setup(commands, account_id, urls)
    for i in range(2):
        caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown', i)
        with execution(urls, account_id, run) as (_, _, token):
            await caps.observe(finish(tool, token))
            await caps.observe(finish(tool, token))
    rows = evidence(owner, run)
    assert len(rows) == 4
    a, b = [r for r in rows if r['criterion_id'] == 'output0'], [r for r in rows if r['criterion_id'] == 'format0']
    bad = [{'criterion_id': 'output0', 'type': 'output_exists', 'ref': str(a[0]['evidence_id'])},
           {'criterion_id': 'format0', 'type': 'check_passed', 'ref': str(next(r for r in b if r['predicate']['subject_ref'] != a[0]['predicate']['subject_ref'])['evidence_id'])}]
    with UnitOfWork(urls[2]) as uow:
        with pytest.raises(ValueError, match='same distinct ResultRef'):
            validate_delivery_evidence(uow, {'account_id': account_id, 'run_id': run}, req.to_dict(), bad)
    finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['status'] == 'completed'


@pytest.mark.parametrize('point', ['bytes_published', 'evidence_inserted', 'observation_committed'])
async def test_recovery_reuses_publication_checks_and_atomic_evidence(commands, account_id, urls, owner, tmp_path, monkeypatch, point):
    wid, run, _ = setup(commands, account_id, urls)
    caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown')
    with execution(urls, account_id, run) as (_, _, token):
        first = caps.facts.record_observation
        if point == 'evidence_inserted':
            monkeypatch.setattr(caps.facts, 'record_observation', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('crash')))
            with pytest.raises(RuntimeError, match='crash'):
                await caps.observe(finish(tool, token))
            assert not evidence(owner, run)
            monkeypatch.setattr(caps.facts, 'record_observation', first)
        if point == 'observation_committed':
            await caps.observe(finish(tool, token))
    # New Activity objects and a new lease simulate a restarted Worker. No model invocation.
    recovered = compose_capabilities(AgentDataStore(urls[2]), services=frozenset({'text'}))
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await recovered.prepare(call(tool, token))
    if prepared.child and not prepared.result_committed:
        text = TextActivities(recovered, OutputPublisher(urls[2], TenantFileStore(tmp_path / 'files', max_bytes=1024*1024)))
        with execution(urls, account_id, run, prepared.child.execution_id) as (_, _, token):
            await text.produce(replace(prepared.child, lease_token=token))
    with execution(urls, account_id, run) as (_, _, token):
        await recovered.observe(finish(tool, token))
        await recovered.observe(finish(tool, token))
    assert len(evidence(owner, run)) == 2
    assert owner.execute('SELECT count(*) n FROM capability_result_refs f JOIN capability_results cr USING(account_id,invocation_id) WHERE cr.run_id=%s', (run,)).fetchone()['n'] == 2
    finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['status'] == 'completed'


@pytest.mark.parametrize('kind', ['check_passed', 'user_acceptance', 'output_exists'])
async def test_other_conditions_remain_independent(commands, account_id, urls, owner, tmp_path, kind):
    extra = ({'id': 'other', 'required': True, 'evidence_types': [kind]},)
    wid, run, _ = setup(commands, account_id, urls, extra=extra)
    caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown')
    with execution(urls, account_id, run) as (_, _, token):
        await caps.observe(finish(tool, token))
    assert len(evidence(owner, run)) == 2
    finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['status'] != 'completed'


async def test_crash_after_bytes_before_result_and_definition_upgrade_recovers(commands, account_id, urls, owner, tmp_path, monkeypatch):
    from agent_activities import composition
    from agent_activities.capabilities import CapabilityActivities
    from agent_activities.text_result import text_definition
    wid, run, _ = setup(commands, account_id, urls)
    original = CapabilityActivities.commit_result
    monkeypatch.setattr(CapabilityActivities, 'commit_result', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('bytes committed')))
    with pytest.raises(RuntimeError, match='bytes committed'):
        await publish(urls, account_id, run, wid, tmp_path, 'markdown')
    assert not evidence(owner, run)
    monkeypatch.setattr(CapabilityActivities, 'commit_result', original)
    monkeypatch.setattr(composition, 'text_definition', lambda: replace(text_definition(), description='Updated trusted definition'))
    caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown')
    with execution(urls, account_id, run) as (_, _, token):
        await caps.observe(finish(tool, token))
    assert len(evidence(owner, run)) == 2
    assert owner.execute('SELECT count(*) n FROM capability_result_refs f JOIN capability_results cr USING(account_id,invocation_id) WHERE cr.run_id=%s', (run,)).fetchone()['n'] == 2
    finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['status'] == 'completed'


@pytest.mark.parametrize('observed', [False, True])
async def test_old_revision_cannot_register_or_complete_and_new_format_is_frozen(commands, account_id, urls, owner, tmp_path, observed):
    from temporalio.exceptions import ApplicationError

    from run_domain.execution import RunNotExecutable
    wid, run, _ = setup(commands, account_id, urls)
    caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown')
    with execution(urls, account_id, run) as (_, _, token):
        old = finish(tool, token)
        if observed:
            await caps.observe(old)
    work = commands.get(account_id, wid)['work']
    commands.revise(account_id, wid, str(uuid4()), work['row_version'], requirement(('docx',)), 'User now requests Word')
    with pytest.raises(ApplicationError) as exc:
        await caps.observe(old)
    assert exc.value.type == 'stale_fencing_token'
    assert len(evidence(owner, run)) == (2 if observed else 0)
    with pytest.raises(RunNotExecutable):
        finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['requirement']['deliverables']['items'][0]['format'] == 'docx'


async def test_failed_format_check_keeps_existence_and_cannot_complete(commands, account_id, urls, owner, tmp_path, monkeypatch):
    monkeypatch.setattr('file_runtime.format_check.verify_format', lambda *a: False)
    wid, run, _ = setup(commands, account_id, urls)
    caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown')
    with execution(urls, account_id, run) as (_, _, token):
        await caps.observe(finish(tool, token))
    rows = evidence(owner, run)
    assert len(rows) == 1 and rows[0]['predicate']['kind'] == 'output_exists'
    finalize(urls, account_id, run, FAKE_CALL)
    assert commands.get(account_id, wid)['work']['status'] != 'completed'


async def test_wrong_format_is_rejected_before_publication_and_save_waits_for_authorization(commands, account_id, urls, owner, tmp_path):
    wid, run, _ = setup(commands, account_id, urls)
    with pytest.raises((AttributeError, TypeError)):
        # prepare returns an explicit error, never a child; no file is produced.
        await publish(urls, account_id, run, wid, tmp_path, 'docx')
    assert not evidence(owner, run)
    work = commands.accept(account_id, str(uuid4()), 'Save too', requirement(extra=(
        {'id': 'save', 'required': True, 'evidence_types': ['workspace_saved']},)), workspace_save_requested=True).body['work']
    reply = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version']).body
    assert 'run' not in reply and reply['work']['status'] != 'completed'


async def test_database_rejects_cross_file_completion_even_with_valid_individual_evidence(commands, account_id, urls, owner, tmp_path):
    import json

    import psycopg
    wid, run, _ = setup(commands, account_id, urls)
    for i in range(2):
        caps, tool = await publish(urls, account_id, run, wid, tmp_path, 'markdown', i)
        with execution(urls, account_id, run) as (_, _, token):
            await caps.observe(finish(tool, token))
    rows = evidence(owner, run)
    exists = next(r for r in rows if r['criterion_id'] == 'output0')
    check = next(r for r in rows if r['criterion_id'] == 'format0' and r['predicate']['subject_ref'] != exists['predicate']['subject_ref'])
    bad = [{'criterion_id': 'output0', 'type': 'output_exists', 'ref': str(exists['evidence_id'])},
           {'criterion_id': 'format0', 'type': 'check_passed', 'ref': str(check['evidence_id'])}]
    finalize(urls, account_id, run, json.dumps({'schema_version': 1, 'kind': 'progress_saved',
        'evidence': [], 'continuation': {'schema_version': 1, 'kind': 'awaiting_input', 'reason': 'explicit_wait'}}))
    receipt = {'schema_version': 1, 'run_id': str(run), 'requirement_revision': 1, 'evidence': bad,
               'evaluator': 'delivery_receipt_policy_v1'}
    with pytest.raises(psycopg.errors.RaiseException, match='same distinct ResultRef'):
        owner.execute("UPDATE works SET status='completed',completed_requirement_revision=1,completion_receipt=%s::jsonb,completed_at=now() WHERE work_id=%s", (json.dumps(receipt), wid))
    assert commands.get(account_id, wid)['work']['status'] == 'active'
