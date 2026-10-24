"""Legacy Generic requirements are repaired only by an audited, versioned revision."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from resources.run_budget import RunBudgetService
from work_domain.commands import WorkConflict
from work_domain.models import LegacyGenericCriteria, Requirement

pytestmark = pytest.mark.postgres
CONTRACT = json.loads((Path(__file__).parents[1] / 'fixtures/work/generic_completion.json').read_text())


@pytest.mark.parametrize('paused', [False, True])
def test_explicit_upgrade_preserves_history_budget_and_task_status(
    commands, account_id, urls, owner, paused
):
    legacy = Requirement.from_dict({**CONTRACT['new_requirement'],
                                    'acceptance_criteria': CONTRACT['legacy_criteria']})
    key = str(uuid4())
    # Seed a historical command through the old writer, never rewrite immutable rows.
    with patch('work_domain.commands.validate_new_completion_criteria'):
        receipt = commands.accept(account_id, key, 'Legacy', legacy)
    work_id = UUID(receipt.body['work']['work_id'])
    replay = commands.accept(account_id, key, 'Legacy', legacy)
    assert replay.replayed and replay.body == receipt.body
    work = commands.get(account_id, work_id)['work']
    run_id = UUID(commands.advance(account_id, work_id, str(uuid4()), work['row_version']).body['run']['run_id'])
    budget = RunBudgetService(urls[2])
    budget.reserve(run_id, 'prior-usage', {'tool_calls': 1})
    budget.settle(run_id, 'prior-usage', {'tool_calls': 1}, 'measured')
    # A failed old attempt is a fact, not a completion or permission to reset usage.
    from run_domain.lifecycle import RunLifecycleService
    lifecycle = RunLifecycleService(urls[2])
    lifecycle.start(account_id, run_id)
    lifecycle.finish(account_id, run_id, 'failed', failure_code='test_failed')
    work = commands.get(account_id, work_id)['work']
    if paused:
        work = commands.control(account_id, work_id, str(uuid4()), work['row_version'], 'pause').body['work']
    before = commands.get(account_id, work_id)['work']
    ledger = owner.execute('SELECT * FROM work_usage_ledger WHERE work_id=%s', (work_id,)).fetchall()
    with pytest.raises(LegacyGenericCriteria):
        commands.revise(account_id, work_id, str(uuid4()), before['row_version'], legacy)
    assert commands.get(account_id, work_id)['work'] == before
    upgraded = Requirement.from_dict({**legacy.to_dict(),
                                      'acceptance_criteria': CONTRACT['upgraded_criteria']})
    revision_key = str(uuid4())
    with pytest.raises(WorkConflict):
        commands.revise(account_id, work_id, revision_key, before['row_version'] - 1, upgraded)
    revised = commands.revise(account_id, work_id, revision_key, before['row_version'], upgraded,
                               'explicit_completion_upgrade').body['work']
    assert revised['status'] == before['status'] == ('paused' if paused else 'active')
    assert revised['completion_receipt'] is None
    assert revised['budget'] == before['budget']
    assert revised['budget']['used']['tool_calls'] == 1
    assert revised['current_requirement_revision'] == 2
    assert revised['requirement']['acceptance_criteria'] == CONTRACT['upgraded_criteria']
    old = owner.execute('SELECT acceptance_criteria FROM work_requirements WHERE work_id=%s AND revision=1',
                        (work_id,)).fetchone()
    assert old['acceptance_criteria'] == CONTRACT['legacy_criteria']
    assert owner.execute('SELECT * FROM work_usage_ledger WHERE work_id=%s', (work_id,)).fetchall() == ledger
    assert owner.execute('SELECT status FROM runs WHERE run_id=%s', (run_id,)).fetchone()['status'] == 'failed'
    replay = commands.revise(account_id, work_id, revision_key, before['row_version'], upgraded,
                              'explicit_completion_upgrade')
    assert replay.replayed and replay.body['work'] == revised


def test_rejected_create_leaves_no_work_or_idempotency_command(commands, account_id, owner):
    key = str(uuid4())
    legacy = Requirement.from_dict({**CONTRACT['new_requirement'],
                                    'acceptance_criteria': CONTRACT['legacy_criteria']})
    with pytest.raises(LegacyGenericCriteria):
        commands.accept(account_id, key, 'Legacy', legacy)
    assert commands.list(account_id)['items'] == []
    assert owner.execute('SELECT count(*) AS n FROM idempotency_commands WHERE account_id=%s',
                          (account_id,)).fetchone()['n'] == 0
    # Correcting a rejected payload is allowed because no command was committed.
    accepted = commands.accept(account_id, key, 'Typed', Requirement.from_dict(CONTRACT['new_requirement']))
    assert accepted.response_status == 201
