"""Shared execution policy stays independent of Temporal and Activity adapters."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

from run_domain.execution_context import execution_fence, fence_scope


def test_business_execution_imports_do_not_load_activity_or_temporal_modules():
    code = """
import importlib
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'agent_activities', 'temporalio'}:
            raise AssertionError('business execution policy reached adapter: ' + fullname)
sys.meta_path.insert(0, Guard())
for name in (
    'run_domain.execution_context', 'run_domain.execution', 'run_domain.delegation',
    'run_domain.results', 'run_domain.capabilities', 'run_domain.capability_contracts',
    'run_domain.diagnostics',
    'run_domain.lifecycle', 'work_domain.completion',
    'work_domain.commands', 'workspace.catalog', 'workspace.resources',
    'workspace.execution', 'file_runtime.output', 'research_domain.persistence',
):
    importlib.import_module(name)
from run_domain.delegation import required_branch_gaps
from run_domain.execution import assert_execution_fence
class Transaction:
    def execute(self, sql, params):
        return self
    def fetchall(self):
        return [{'branch_key': 'required-investigation'}]
assert required_branch_gaps(Transaction(), 'account', 'run') == ['required-investigation']
assert_execution_fence(Transaction())  # An unfenced caller has no Activity context.
"""
    root = Path(__file__).resolve().parents[1]
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        check=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )


async def test_execution_identity_isolated_between_tasks_threads_and_nested_scopes():
    async def branch(token):
        account, run, execution = (str(uuid4()) for _ in range(3))
        expected = (account, run, execution, token)
        with fence_scope(account, run, token, execution):
            await asyncio.sleep(0)
            assert await asyncio.to_thread(execution_fence.get) == expected
            try:
                with fence_scope(account, run, token + 1, str(uuid4())):
                    raise RuntimeError("nested attempt failed")
            except RuntimeError:
                pass
            assert execution_fence.get() == expected
        assert execution_fence.get() is None

    assert execution_fence.get() is None
    await asyncio.gather(branch(1), branch(2))
    assert execution_fence.get() is None


def test_capability_value_import_does_not_load_database_or_schema_runtime():
    root = Path(__file__).resolve().parents[1]
    code = """
import sys
from run_domain.capability_contracts import CapabilityObservation
assert CapabilityObservation('caller', 'producer', 'capability-result:caller').to_dict()['schema_version'] == 1
assert not {'jsonschema', 'psycopg', 'temporalio', 'agent_activities'} & sys.modules.keys()
"""
    subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        check=True,
        env={**os.environ, "PYTHONPATH": str(root / "src")},
    )


async def test_unresolved_operation_is_not_automatically_retried_by_an_activity():
    from types import SimpleNamespace

    import pytest
    from temporalio.exceptions import ApplicationError

    from agent_activities.fencing import fenced_activity
    from run_domain.results import OperationUncertain

    @fenced_activity
    async def enter(_self, _request):
        raise OperationUncertain("original external write is unresolved")

    request = SimpleNamespace(
        account_id=str(uuid4()), run_id=str(uuid4()), execution_id=str(uuid4()),
        lease_token=1, operation_id="original-operation",
    )
    with pytest.raises(ApplicationError) as error:
        await enter(None, request)
    assert error.value.type == "tool_side_effect_uncertain"
    assert error.value.non_retryable
    assert execution_fence.get() is None


async def test_fenced_control_failure_has_safe_exact_identity_and_stage():
    from types import SimpleNamespace

    import pytest
    from temporalio.exceptions import ApplicationError

    from agent_activities.fencing import fenced_activity
    from resources.run_budget import CapabilityBudgetExhausted

    @fenced_activity
    async def produce(_self, _request):
        raise CapabilityBudgetExhausted("private provider request and credential")

    request = SimpleNamespace(
        account_id=str(uuid4()), run_id=str(uuid4()), execution_id=str(uuid4()),
        lease_token=17, execution_attempt=3, operation_id="stable-operation",
    )
    with pytest.raises(ApplicationError) as failure:
        await produce(None, request)
    error = failure.value
    assert error.non_retryable and error.type == "capability_budget_exhausted"
    assert "private provider" not in str(error)
    assert error.__cause__ is None  # Temporal must not serialize a sensitive cause chain.
    assert error.details == ({
        "schema_version": 1, "boundary": "activity", "phase": "produce",
        "run_id": request.run_id, "execution_id": request.execution_id,
        "operation_id": request.operation_id, "fencing_token": 17,
        "execution_attempt": 3,
    },)
    assert execution_fence.get() is None


async def test_preconverted_control_error_is_sanitized_without_changing_retry_contract():
    from types import SimpleNamespace

    import pytest
    from temporalio.api.failure.v1 import Failure
    from temporalio.converter import DefaultFailureConverter, DefaultPayloadConverter
    from temporalio.exceptions import ApplicationError

    from agent_activities.fencing import fenced_activity
    from resources.run_budget import RunBudgetExhausted

    @fenced_activity
    async def decide(_self, _request):
        try:
            raise RunBudgetExhausted('PRIVATE-PROVIDER-INPUT')
        except RunBudgetExhausted as cause:
            raise ApplicationError('PRIVATE-PROVIDER-MESSAGE', {'secret': 'PRIVATE-KEY'},
                                   type='run_budget_exhausted', non_retryable=False) from cause

    request = SimpleNamespace(account_id='account', run_id='run', execution_id='execution',
                              operation_id='operation', lease_token=4, execution_attempt=1)
    with pytest.raises(ApplicationError) as failed:
        await decide(None, request)
    assert failed.value.non_retryable is False
    failure = Failure()
    DefaultFailureConverter().to_failure(failed.value, DefaultPayloadConverter(), failure)
    assert b'PRIVATE-' not in failure.SerializeToString()
    assert failed.value.details[0]['execution_id'] == 'execution'


def test_agent_workflow_import_closure_cannot_reach_runtime_adapters():
    """Guard future modules as well as current Workflow/helper implementations."""
    root = Path(__file__).resolve().parents[1]
    code = """
import importlib
import importlib.abc
import pkgutil
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {
            'agent_activities', 'research_activities', 'application', 'bootstrap',
            'persistence', 'resources', 'web_api', 'web_artifacts', 'sandbox',
        }:
            raise AssertionError('Workflow dependency reached runtime adapter: ' + fullname)
sys.meta_path.insert(0, Guard())
import agent_workflows
for module in pkgutil.walk_packages(agent_workflows.__path__, 'agent_workflows.'):
    importlib.import_module(module.name)
# Shared lifecycle and fixed Research orchestration use the same import rule.
for name in ('orchestration.agent_lifecycle_workflow', 'orchestration.research_workflow', 'memory.workflows'):
    importlib.import_module(name)
"""
    subprocess.run([sys.executable, '-c', code], cwd=root, check=True,
                   env={**os.environ, 'PYTHONPATH': str(root / 'src')})
