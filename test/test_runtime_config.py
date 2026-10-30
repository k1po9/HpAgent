"""Runtime deployment parameters reach consumers without changing default policy."""
from __future__ import annotations

import os
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from common.runtime_config import (
    ArtifactRuntimeConfig,
    CapacityConfig,
    FileRuntimeConfig,
    binding_challenge_seconds,
    run_budget_mode,
)
from orchestration.config import AppConfig
from resources.capacity import CapacityService
from web_api.config import WebApiSettings


@pytest.fixture(autouse=True)
def isolated_environment():
    with patch.dict(os.environ, {}, clear=True):
        yield


def test_capacity_limits_accept_explicit_values_and_preserve_zero_and_live_reads(monkeypatch):
    monkeypatch.setenv('CAPACITY_MODEL_GLOBAL', '5')
    monkeypatch.setenv('CAPACITY_MODEL_ACCOUNT', '2')
    monkeypatch.setenv('CAPACITY_MODEL_INTERACTIVE_RESERVED', '7')
    config = CapacityConfig.from_environment()
    assert config.resource_limits('model') == (5, 7, 2)
    monkeypatch.setenv('WORK_GLOBAL_COORDINATORS', '0')
    assert CapacityConfig.from_environment().work_global_coordinators == 0
    assert config.work_global_coordinators == 32
    monkeypatch.setenv('WORK_GLOBAL_COORDINATORS', '11')
    assert CapacityConfig.from_environment().work_global_coordinators == 11


@pytest.mark.parametrize('name', [
    'WORK_ACCOUNT_ACCEPT_LIMIT', 'WORK_ACCOUNT_COORDINATORS', 'WORK_GLOBAL_COORDINATORS',
    'WORK_ACCOUNT_SUBAGENTS', 'WORK_GLOBAL_SUBAGENTS',
    'CAPABILITY_ACCOUNT_EXECUTIONS', 'CAPABILITY_GLOBAL_EXECUTIONS',
    'DELIVERY_ACCOUNT_CAPACITY', 'DELIVERY_GLOBAL_CAPACITY',
    'CAPACITY_MODEL_GLOBAL', 'CAPACITY_TOOL_ACCOUNT', 'CAPACITY_FETCH_INTERACTIVE_RESERVED',
])
@pytest.mark.parametrize('value', ['-1', 'secret-invalid', '1.5'])
def test_capacity_config_rejects_invalid_values_with_safe_fields(name, value):
    with pytest.raises(ValueError, match=name) as error:
        CapacityConfig.from_environment({name: value})
    assert 'secret-invalid' not in str(error.value)
    assert error.value.__cause__ is None


@pytest.mark.parametrize(('factory', 'name', 'value'), [
    (FileRuntimeConfig, 'FILE_MAX_BYTES', '1073741825'),
    (FileRuntimeConfig, 'FILE_MAX_COUNT_PER_MESSAGE', '21'),
    (FileRuntimeConfig, 'FILE_DIRECT_READ_MAX_BYTES', '0'),
    (FileRuntimeConfig, 'FILE_CLEANUP_INTERVAL_SECONDS', '0'),
    (ArtifactRuntimeConfig, 'ARTIFACT_HTML_MAX_BYTES', '0'),
    (ArtifactRuntimeConfig, 'ARTIFACT_MODEL_READ_TIMEOUT_SECONDS', '4'),
    (ArtifactRuntimeConfig, 'ARTIFACT_MODEL_READ_TIMEOUT_SECONDS', '301'),
    (ArtifactRuntimeConfig, 'ARTIFACT_MODEL_READ_TIMEOUT_SECONDS', 'nan'),
    (ArtifactRuntimeConfig, 'ARTIFACT_MODEL_READ_TIMEOUT_SECONDS', 'secret-invalid'),
])
def test_file_and_artifact_configuration_preserve_bounds_and_safe_errors(factory, name, value):
    with pytest.raises(ValueError, match=name) as error:
        factory.from_environment({name: value})
    assert 'secret-invalid' not in str(error.value)


def test_budget_modes_keep_each_callers_default_and_identity_ttl_is_checked():
    assert run_budget_mode({}, default='observe') == 'observe'
    assert run_budget_mode({}, default='enforce') == 'enforce'
    assert run_budget_mode({'RUN_BUDGET_MODE': 'off'}, default='enforce') == 'off'
    with pytest.raises(ValueError, match='RUN_BUDGET_MODE'):
        run_budget_mode({'RUN_BUDGET_MODE': 'secret-invalid'}, default='observe')
    assert binding_challenge_seconds({'QQ_BINDING_CHALLENGE_SECONDS': '99'}) == 99
    with pytest.raises(ValueError, match='QQ_BINDING_CHALLENGE_SECONDS'):
        binding_challenge_seconds({'QQ_BINDING_CHALLENGE_SECONDS': '0'})


class Cursor:
    def __init__(self, value):
        self.value = value

    def fetchone(self):
        return self.value


class CapacityTransaction:
    def __init__(self, *, total=0):
        self.total = total
        self.queries = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=()):
        self.queries.append((query, params))
        if 'SELECT r.*' in query:
            return Cursor({'status': 'running', 'account_status': 'active',
                           'work_id': None, 'account_id': uuid4()})
        if 'AS total' in query:
            return Cursor({'total': self.total, 'background': 0, 'account': 0, 'account_background': 0})
        if 'count(*) AS n' in query:
            return Cursor({'n': self.total})
        return Cursor(None)


def test_resource_capacity_parameter_controls_real_acquire_branch(monkeypatch):
    from resources import capacity

    transaction = CapacityTransaction(total=2)
    monkeypatch.setattr(capacity, 'UnitOfWork', lambda _: transaction)
    monkeypatch.setenv('CAPACITY_MODEL_GLOBAL', '2')
    assert CapacityService(object()).acquire(uuid4(), 'model', uuid4()) is False
    assert not any('SELECT q.ticket_id' in query for query, _ in transaction.queries)


def test_delivery_parameters_control_claim_and_account_query(monkeypatch):
    from delivery import service

    transaction = CapacityTransaction()
    monkeypatch.setattr(service, 'UnitOfWork', lambda _: transaction)
    monkeypatch.setenv('DELIVERY_GLOBAL_CAPACITY', '0')
    assert service.DeliveryService(object()).claim() is None
    assert not any('SELECT d.delivery_id' in query for query, _ in transaction.queries)
    monkeypatch.setenv('DELIVERY_GLOBAL_CAPACITY', '10')
    monkeypatch.setenv('DELIVERY_ACCOUNT_CAPACITY', '2')
    assert service.DeliveryService(object()).claim() is None
    assert next(params for query, params in transaction.queries if 'SELECT d.delivery_id' in query) == (2,)


@pytest.mark.asyncio
async def test_invalid_worker_parameters_fail_before_dependency_or_filesystem_setup(monkeypatch):
    from bootstrap import worker_dependencies

    async def forbidden(*_):
        raise AssertionError('invalid configuration reached dependency setup')

    monkeypatch.setenv('WORK_GLOBAL_COORDINATORS', '-1')
    monkeypatch.setattr(worker_dependencies, '_init_dependencies', forbidden)
    with pytest.raises(ValueError, match='WORK_GLOBAL_COORDINATORS'):
        await worker_dependencies.init_dependencies(AppConfig())


@pytest.mark.asyncio
async def test_invalid_file_flags_fail_before_worker_dependency_setup(monkeypatch):
    from bootstrap import worker_dependencies
    from workspace.file_scope import RunFileScopeUnavailable

    async def forbidden(*_):
        raise AssertionError('invalid file flags reached dependency setup')

    monkeypatch.setenv('WEB_FILE_UPLOAD_ENABLED', 'false')
    monkeypatch.setenv('WEB_FILE_TRANSFORM_ENABLED', 'true')
    monkeypatch.setattr(worker_dependencies, '_init_dependencies', forbidden)
    with pytest.raises(RunFileScopeUnavailable, match='require file uploads'):
        await worker_dependencies.init_dependencies(AppConfig())


def test_production_file_budget_errors_preserve_the_file_configuration_contract(tmp_path):
    from workspace.file_capability_config import FileCapabilityConfig
    from workspace.file_scope import RunFileScopeUnavailable

    with pytest.raises(RunFileScopeUnavailable, match='RUN_BUDGET_MODE') as error:
        FileCapabilityConfig.from_environment(tmp_path, None, {
            'HPAGENT_ENV': 'production', 'WEB_FILE_UPLOAD_ENABLED': 'true',
            'RUN_BUDGET_MODE': 'secret-invalid',
        })
    assert 'secret-invalid' not in str(error.value)


def test_invalid_api_capacity_fails_before_database_io(monkeypatch):
    from web_api import lifespan
    from web_api.app import create_app

    def forbidden(*_, **__):
        raise AssertionError('invalid configuration reached database setup')

    monkeypatch.setenv('APP_DATABASE_URL', 'postgresql://unused')
    monkeypatch.setenv('WORK_ACCOUNT_ACCEPT_LIMIT', '-1')
    monkeypatch.setattr(lifespan, 'ConnectionPool', forbidden)
    with pytest.raises(ValueError, match='WORK_ACCOUNT_ACCEPT_LIMIT'):
        with TestClient(create_app(WebApiSettings.from_env())):
            pass


@pytest.mark.asyncio
async def test_artifact_values_reach_formal_generator_composition(monkeypatch):

    from web_artifacts import generator

    original = generator.WebArtifactGenerator
    created = []

    def capture(*args, **kwargs):
        instance = original(*args, **kwargs)
        created.append(instance)
        return instance

    monkeypatch.setenv('ARTIFACT_HTML_MAX_BYTES', '12345')
    monkeypatch.setenv('ARTIFACT_MODEL_READ_TIMEOUT_SECONDS', '71.5')
    monkeypatch.setattr(generator, 'WebArtifactGenerator', capture)
    from pathlib import Path
    with monkeypatch.context() as context:
        context.syspath_prepend(str(Path(__file__).resolve().parents[1] / 'scripts/check'))
        from temporal_registry import capture_registries
        await capture_registries()
    assert created
    assert created[0].max_bytes == 12345
    assert created[0].read_timeout_seconds == 71.5
