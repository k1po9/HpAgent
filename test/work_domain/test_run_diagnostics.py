"""Read-only diagnostic identities over the real, isolated execution fact tables."""
import json
from uuid import uuid4

import pytest
from test_capability_contracts import definition
from test_capability_facts import begin, chat, execution

from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityRegistry
from run_domain.results import ResultReceiptService
from web_domain.errors import ResourceNotFound

pytestmark = pytest.mark.postgres


def test_diagnostics_exposes_registered_attempts_and_frozen_capability_not_inputs(urls, account_id, owner):
    from run_domain.diagnostics import run_diagnostics

    run = chat(urls, account_id)
    facts = CapabilityFacts(CapabilityRegistry((definition(),)))
    with execution(urls, account_id, run) as (store, parent, token):
        invocation = begin(facts, urls, store, run, 'invoke', inputs={'text': 'sensitive-memory-body'})
        operation = invocation['invocation_id']
        ResultReceiptService(urls[2]).receive(
            str(account_id), str(run), parent.execution_id, operation, token,
            {'content': 'sensitive-model-input', 'api_key': 'sensitive-credential'},
        )
    with execution(urls, account_id, run) as (store, parent, newer_token):
        assert newer_token > token
        store.begin_tool_operation(operation, str(run))
    owner.execute("UPDATE execution_operations SET error_code='capability_budget_exhausted' WHERE operation_id=%s", (operation,))
    before = owner.execute('SELECT count(*) AS n FROM execution_operations').fetchone()['n']
    result = run_diagnostics(urls[1], account_id, run)
    assert result['schema_version'] == 1
    assert result['run']['run_id'] == str(run)
    assert result['run']['config_versions']['strategy_policy_version'] == 1
    assert result['run']['config_versions']['agent_tools'] is None
    assert len(result['executions']['items']) == 2
    root = next(e for e in result['executions']['items'] if e['role'] == 'root')
    assert root['recorded_attempt_no'] == newer_token
    assert 'execution_attempt' not in root
    records = [r for r in result['operations']['items'] if r['operation_id'] == operation]
    assert len(records) == 2
    old, current = sorted(records, key=lambda row: row['attempt_no'])
    assert old['fencing_token'] == token and current['fencing_token'] == newer_token
    assert old['is_latest_registered_attempt'] is False
    assert current['is_latest_registered_attempt'] is True
    # Operation error belongs to current state, never retrospectively assigned to an old attempt.
    assert old['operation_error_code'] is None
    assert current['operation_error_code'] == 'capability_budget_exhausted'
    assert old['receipt_disposition'] == 'current'  # a historical fact, not current authority
    assert current['receipt_disposition'] is None
    assert current['capability']['key'] == 'test.echo'
    assert current['capability']['contract_version'] == 1
    assert current['capability']['implementation_version'] == '1'
    assert len(current['capability']['descriptor_digest']) == 64
    assert 'sensitive-' not in json.dumps(result)
    assert owner.execute('SELECT count(*) AS n FROM execution_operations').fetchone()['n'] == before
    with pytest.raises(ResourceNotFound):
        run_diagnostics(urls[1], uuid4(), run)


def test_diagnostics_empty_run_and_bounded_attempt_history(urls, account_id, owner):
    from run_domain.diagnostics import run_diagnostics

    run = chat(urls, account_id)
    assert run_diagnostics(urls[1], account_id, run)['operations'] == {'items': [], 'truncated': False}
    with execution(urls, account_id, run) as (store, _, _):
        for number in range(102):
            store.begin_tool_operation(f'diagnostic:{number}', str(run))
    result = run_diagnostics(urls[1], account_id, run)
    assert len(result['operations']['items']) == 100
    assert result['operations']['truncated'] is True
    assert result['executions']['truncated'] is False


def test_api_attempt_read_grant_does_not_allow_payload_or_writes(urls):
    import psycopg

    with psycopg.connect(urls[1], autocommit=True) as connection:
        connection.execute('SET search_path TO hpagent, public')
        connection.execute('SELECT account_id,operation_id,attempt_no,fencing_token FROM execution_operation_attempts LIMIT 0')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('SELECT receipt_ref FROM execution_operation_attempts LIMIT 0')
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            connection.execute('UPDATE execution_operation_attempts SET fencing_token=fencing_token WHERE false')



def test_run_failure_before_any_operation_keeps_safe_terminal_code(urls, account_id):
    from run_domain.diagnostics import run_diagnostics
    from run_domain.lifecycle import RunLifecycleService

    run = chat(urls, account_id)
    RunLifecycleService(urls[2]).finish(account_id, run, 'failed', failure_code='worker_lost',
                                      failure_message='PRIVATE-PROVIDER-MESSAGE')
    result = run_diagnostics(urls[1], account_id, run)
    assert result['run']['failure_code'] == 'worker_lost'
    assert result['operations']['items'] == []
    assert 'PRIVATE-' not in json.dumps(result)
