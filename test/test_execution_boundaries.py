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
    'run_domain.results', 'run_domain.lifecycle', 'work_domain.completion',
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
