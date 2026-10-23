"""Paid attempts reuse real Run/Work ledgers; no external provider requests."""
import asyncio
from uuid import UUID

import pytest
from support.work_fixtures import accept_research, advance_work
from uuid6 import uuid7

from resources.run_budget import RunBudgetExhausted, RunBudgetService
from resources.work_budget import WorkBudgetExhausted
from web_retrieval.budget import RetrievalBudget, RetrievalUsageUnknown
from work_domain.commands import WorkCommandService

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


def start(database_url, account_id):
    commands = WorkCommandService(database_url, budget_mode='enforce')
    work = accept_research(commands, account_id, str(uuid7()), 'Retrieval', 'Search official docs')
    work_id = UUID(work.body['work']['work_id'])
    run = advance_work(commands, account_id, work_id, str(uuid7()))
    return work_id, UUID(run.body['run']['run_id'])


async def test_paid_charge_is_one_attempt_not_candidate_count_and_replay_is_uncertain(
        db, database_url, worker_database_url, account_id):
    work_id, run_id = start(database_url, account_id)
    budget = RetrievalBudget(RunBudgetService(worker_database_url), run_id, 'retrieval:stable')
    results = await asyncio.gather(budget.before_request('search'), budget.before_request('search'),
                                   return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, RetrievalUsageUnknown) for result in results) == 1
    run_used = db.execute('SELECT used FROM run_budgets WHERE run_id=%s', (run_id,)).fetchone()[0]
    work_used = db.execute('SELECT used FROM work_budgets WHERE work_id=%s', (work_id,)).fetchone()[0]
    assert run_used['tool_calls'] == work_used['tool_calls'] == 1
    with pytest.raises(RetrievalUsageUnknown):
        await budget.before_request('search')
    row = db.execute('SELECT state,actual_amount,usage_source FROM run_usage_ledger '
                     'WHERE run_id=%s AND operation_id=%s',
                     (run_id, 'retrieval:stable:paid:search:v1')).fetchone()
    assert row == ('settled', 1, 'estimated')


@pytest.mark.parametrize('which', ['run', 'work'])
async def test_paid_request_respects_existing_run_and_work_caps(
        which, db, database_url, worker_database_url, account_id):
    work_id, run_id = start(database_url, account_id)
    if which == 'run':
        db.execute("UPDATE run_budgets SET limits=jsonb_set(limits,'{tool_calls}','0') WHERE run_id=%s", (run_id,))
    else:
        db.execute("UPDATE work_budgets SET limits=jsonb_set(limits,'{tool_calls}','0') WHERE work_id=%s", (work_id,))
    budget = RetrievalBudget(RunBudgetService(worker_database_url), run_id, 'retrieval:denied')
    with pytest.raises(WorkBudgetExhausted if which == 'work' else RunBudgetExhausted):
        await budget.before_request('search')
    assert db.execute('SELECT count(*) FROM run_usage_ledger WHERE run_id=%s AND operation_id=%s',
                      (run_id, 'retrieval:denied:paid:search:v1')).fetchone()[0] == 0


async def test_unsettled_send_identity_is_never_reissued(
        db, database_url, worker_database_url, account_id):
    _, run_id = start(database_url, account_id)
    service = RunBudgetService(worker_database_url)
    key = 'retrieval:interrupted:paid:search:v1'
    service.reserve(run_id, key, {'tool_calls': 1})
    with pytest.raises(RetrievalUsageUnknown):
        await RetrievalBudget(service, run_id, 'retrieval:interrupted').before_request('search')
    assert db.execute('SELECT state FROM run_usage_ledger WHERE run_id=%s AND operation_id=%s',
                      (run_id, key)).fetchone()[0] == 'reserved'


async def test_paid_cap_is_strict_even_when_chat_snapshot_is_observe(
        db, database_url, worker_database_url, account_id):
    from conversation_domain.commands import CommandService

    commands = CommandService(database_url, budget_mode='observe', budget_limits={'tool_calls': 0})
    conversation = UUID(commands.create_conversation(account_id, str(uuid7()))['conversation_id'])
    run_id = UUID(commands.send_message(account_id, conversation, str(uuid7()), 'Search docs')['run_id'])
    with pytest.raises(RunBudgetExhausted):
        await RetrievalBudget(RunBudgetService(worker_database_url), run_id, 'observe:paid').before_request('search')
    assert db.execute('SELECT count(*) FROM run_usage_ledger WHERE run_id=%s', (run_id,)).fetchone()[0] == 0
    assert db.execute('SELECT mode FROM run_budgets WHERE run_id=%s', (run_id,)).fetchone()[0] == 'observe'
