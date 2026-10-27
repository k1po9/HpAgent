from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest

from work_domain.models import Requirement
from work_domain.time_policy import WorkTimeInPast

pytestmark = pytest.mark.postgres


def once(due):
    return Requirement('Meeting', 'reminder', {'schema_version': 1, 'content': 'Meeting'},
                       timing={'schema_version': 1, 'kind': 'once', 'timezone': 'Asia/Shanghai',
                               'due_at': due.isoformat()})


def test_past_create_rolls_back_key_and_can_correct(commands, account_id, owner):
    key = str(uuid4())
    with pytest.raises(WorkTimeInPast):
        commands.accept(account_id, key, 'Meeting', once(datetime.now(UTC) - timedelta(days=365)))
    assert owner.execute('SELECT count(*) AS n FROM works').fetchone()['n'] == 0
    assert owner.execute('SELECT count(*) AS n FROM idempotency_commands WHERE idempotency_key=%s',
                         (key,)).fetchone()['n'] == 0
    result = commands.accept(account_id, key, 'Meeting', once(datetime.now(UTC) + timedelta(days=1)))
    assert result.response_status == 201


def test_expired_command_replay_and_unchanged_schedule_revision(commands, account_id, owner):
    due = datetime.now(UTC) + timedelta(hours=1)
    req = once(due)
    key = str(uuid4())
    accepted = commands.accept(account_id, key, 'Meeting', req)
    with patch('work_domain.time_policy.datetime', wraps=datetime) as clock:
        clock.now.return_value = due + timedelta(hours=1)
        replay = commands.accept(account_id, key, 'Meeting', req)
        assert replay.replayed and replay.body == accepted.body
        work = accepted.body['work']
        revised = commands.revise(account_id, UUID(work['work_id']), str(uuid4()),
                                  work['row_version'], replace(req, objective='Changed content'))
        assert revised.body['work']['current_requirement_revision'] == 2
        key = str(uuid4())
        before = commands.get(account_id, UUID(work['work_id']))
        with pytest.raises(WorkTimeInPast):
            commands.revise(account_id, UUID(work['work_id']), key,
                            revised.body['work']['row_version'], once(due - timedelta(minutes=1)))
        assert commands.get(account_id, UUID(work['work_id'])) == before
        assert owner.execute('SELECT count(*) AS n FROM idempotency_commands WHERE idempotency_key=%s',
                             (key,)).fetchone()['n'] == 0


def test_context_clock_survives_failed_attempt(commands, account_id, urls, owner):
    from agent_activities.store import AgentDataStore
    from run_domain.execution_context import fence_scope
    from run_domain.lifecycle import RunLifecycleService

    req = Requirement('Investigate', 'generic_work', {'schema_version': 1})
    work = commands.accept(account_id, str(uuid4()), 'Investigate', req).body['work']
    run = commands.advance(account_id, UUID(work['work_id']), str(uuid4()), work['row_version']).body['run']
    RunLifecycleService(urls[2]).start(account_id, UUID(run['run_id']))
    store = AgentDataStore(urls[2])
    lease = store.acquire_lease(str(account_id), run['run_id'])
    operation = f'{lease.execution_id}:context'
    with fence_scope(str(account_id), run['run_id'], lease.fencing_token, lease.execution_id):
        store.begin_operation(operation, run['run_id'], 'context')
        started = store.context_started_at(operation)
        store.fail_operation(operation, 'context_build_failed')
        store.begin_operation(operation, run['run_id'], 'context')
        assert store.context_started_at(operation) == started
        second = f'{lease.execution_id}:second-context'
        store.begin_operation(second, run['run_id'], 'context')
        assert store.context_started_at(second) > started
    row = owner.execute('SELECT attempt_count FROM execution_operations WHERE operation_id=%s',
                         (operation,)).fetchone()
    assert row['attempt_count'] == 2
