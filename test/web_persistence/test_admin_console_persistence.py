"""Admin queries and SQL transaction behavior against the dedicated test DB."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from psycopg.rows import dict_row

pytestmark = pytest.mark.postgres


@pytest.fixture
def console(monkeypatch, migration_database_url):
    path = Path(__file__).parents[2] / 'scripts/operations/admin-console.py'
    spec = importlib.util.spec_from_file_location('admin_console_persistence', path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    # Reuse only the explicitly configured disposable DB; never the script's
    # default local deployment port or its .env credentials.
    monkeypatch.setattr(module, 'connect_app', lambda *_: psycopg.connect(
        migration_database_url, row_factory=dict_row,
    ))
    monkeypatch.setattr(module, 'migrate_conninfo', lambda: migration_database_url)
    return module


def test_admin_fixed_queries_and_invite_creation_match_current_schema(console, db, account_id):
    created = console.create_invite({'daily_token_limit': 5000})
    invites = console.list_invites()['invites']
    assert len(invites) == 1
    assert invites[0]['invite_id'] == UUID(created['invite_id'])
    assert invites[0]['entitlement_profile']['daily_token_limit'] == 5000
    assert 'invite_code' not in invites[0] and 'code_hash' not in invites[0]
    users = console.list_users()['users']
    assert len(users) == 1 and users[0]['account_id'] == account_id
    detail = console.user_detail(str(account_id))
    assert detail['account']['account_id'] == account_id
    assert detail['counts'] == {'conversations': 0, 'messages': 0, 'works': 0}
    assert not detail['credentials'] and not detail['sessions']


def test_sql_batch_commits_results_and_rolls_back_later_errors(console, db, account_id):
    target = console.SQL_TARGET_BY_KEY['app-migrate']
    update = f"UPDATE accounts SET version=version+1 WHERE account_id='{account_id}'"
    result = console.execute_tcp(target, update + '; SELECT generate_series(1,502) AS n; SELECT 2 AS n')
    assert result['kind'] == 'batch'
    command, large, small = result['results']
    assert command['rowcount'] == 1
    assert len(large['rows']) == 500 and large['truncated']
    assert small['rows'] == [{'n': 2}] and not small['truncated']
    assert db.execute('SELECT version FROM accounts WHERE account_id=%s', (account_id,)).fetchone()[0] == 2
    with pytest.raises(console.ConsoleError, match='division by zero'):
        console.execute_tcp(target, update + '; SELECT 1/0')
    assert db.execute('SELECT version FROM accounts WHERE account_id=%s', (account_id,)).fetchone()[0] == 2


def test_explicit_commit_can_persist_before_a_later_batch_error(console, db, account_id):
    with pytest.raises(console.ConsoleError, match='division by zero'):
        console.execute_tcp(
            console.SQL_TARGET_BY_KEY['app-migrate'],
            f"UPDATE accounts SET version=version+1 WHERE account_id='{account_id}'; COMMIT; SELECT 1/0",
        )
    assert db.execute('SELECT version FROM accounts WHERE account_id=%s', (account_id,)).fetchone()[0] == 2
