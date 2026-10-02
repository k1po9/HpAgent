from __future__ import annotations

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg.rows import dict_row

from persistence.migrate import migrate, verify_schema
from work_domain.commands import WorkCommandService


@pytest.fixture(scope='session')
def urls():
    names = ('MIGRATION_DATABASE_URL', 'APP_DATABASE_URL', 'WORKER_DATABASE_URL')
    if not all(os.getenv(name) for name in names):
        pytest.skip('three real PostgreSQL role URLs are required')
    values = tuple(os.environ[name] for name in names)
    migrate(values[0])
    for url in values:
        verify_schema(url)
    return values


@pytest.fixture
def owner(urls):
    with psycopg.connect(urls[0], autocommit=True, row_factory=dict_row) as connection:
        connection.execute('SET search_path TO hpagent, public')
        yield connection


@pytest.fixture
def account_id(owner):
    value = uuid4()
    owner.execute('INSERT INTO accounts(account_id) VALUES (%s)', (value,))
    return value


@pytest.fixture
def commands(urls):
    return WorkCommandService(urls[1])
