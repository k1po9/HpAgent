"""Existing child factories inherit already-read context; no Work Recall is added."""
from uuid import UUID

import pytest
from test_delegation import brief, request, setup

from agent_activities.delegation import DelegationActivities
from persistence.memory_context import bind_context, check_execution
from persistence.uow import UnitOfWork
from resources.run_budget import RunBudgetService
from run_domain.execution_context import fence_scope


@pytest.mark.postgres
def test_delegation_children_inherit_context_without_work_recall(owner, urls, commands, account_id):
    _, run, store, root, segment, token = setup(commands, account_id, urls)
    scopes = [['scope:group', 'group:B']]
    with UnitOfWork(urls[2]) as uow:
        bind_context(uow, account_id, root, scopes, {'inherited-context'}, 0)
    activities = DelegationActivities(store, RunBudgetService(urls[2]))
    with fence_scope(str(account_id), run['run_id'], token, root):
        req = request(store, account_id, run, root, [brief()])
        children = activities._prepare(req, [brief()])
    child = children[0].execution_id
    with UnitOfWork(urls[2]) as uow:
        assert check_execution(uow, account_id, UUID(run['run_id']), child)['scopes'] == scopes
    store.release_segment(segment)
