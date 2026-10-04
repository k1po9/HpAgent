"""Extraction must preserve behavior without loading retired runtime owners."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from temporalio import activity, workflow

from memory.activities import ScheduledMemoryActivities
from memory.maintenance import HindsightMaintenance
from memory.workflows import MetricsReportWorkflow, ReflectWorkflow
from orchestration.execution_strategy import WorkExecutionPlanner
from orchestration.run_dispatcher import ReminderActivities


def test_canonical_imports_do_not_load_retired_runtime_owners():
    # A fresh interpreter catches eager package exports that a shared pytest
    # process (with legacy compatibility tests already imported) would hide.
    code = '''
import importlib
import importlib.abc
import sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'agent', 'agent_execution', 'harness'} or fullname in {
            'orchestration.workflow', 'orchestration.web_workflow',
            'orchestration.web_activities', 'orchestration.scheduler',
            'application.scheduler', 'sandbox.tools.local.reminder',
        }:
            raise AssertionError('canonical import reached retired owner: ' + fullname)
sys.meta_path.insert(0, Guard())
for name in (
    'main', 'web_api.app', 'orchestration.document_worker',
    'agent_activities.runtime', 'document_activities.runtime', 'research_activities.runtime',
    'memory.activities', 'memory.workflows', 'orchestration.run_dispatcher',
):
    importlib.import_module(name)
from application.prompts import PromptLoader
PromptLoader()
from actions.contracts import ActionRequest, ActionResult
from brain.contracts import BrainDecision
from application.execution_contracts import ExecutionRequest, StableExecutionFailure
assert ActionRequest.__module__ == 'actions.contracts'
assert ActionResult.__module__ == 'actions.contracts'
assert BrainDecision.__module__ == 'brain.contracts'
assert ExecutionRequest.__module__ == 'application.execution_contracts'
assert StableExecutionFailure.__module__ == 'application.execution_contracts'
'''
    root = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-c", code], cwd=root, check=True,
                   env={**os.environ, "PYTHONPATH": str(root / "src")})


@pytest.mark.asyncio
async def test_extracted_scheduled_services_preserve_names_and_account_isolation():
    from application.memory_reflection import MemoryReflectionService
    from application.metrics import MetricsSnapshotService

    class Hindsight:
        async def reflect(self, account_id):
            if account_id == "failed":
                raise RuntimeError("unavailable")
            return 4

        def get_metrics(self):
            return {"recall_calls": 7}

    maintenance = HindsightMaintenance(Hindsight())
    activities = ScheduledMemoryActivities(
        memory_reflection=MemoryReflectionService(maintenance),
        metrics=MetricsSnapshotService(maintenance),
    )
    assert await activities.reflect_batch(["first", "failed", "last"]) == {
        "results": {"first": 4, "failed": -1, "last": 4}, "total": 3,
    }
    assert await activities.metrics_report() == {"recall_calls": 7}
    assert activity._Definition.from_callable(activities.reflect_batch).name == "reflect_batch_activity"
    assert activity._Definition.from_callable(activities.metrics_report).name == "metrics_report_activity"
    assert workflow._Definition.from_class(ReflectWorkflow).name == "ReflectWorkflow"
    assert workflow._Definition.from_class(MetricsReportWorkflow).name == "MetricsReportWorkflow"


@pytest.mark.asyncio
async def test_scheduled_activity_collections_keep_instance_owned_dependencies():
    class Reflection:
        def __init__(self, marker):
            self.marker = marker

        async def reflect(self, account_id):
            return {"marker": self.marker, "account_id": account_id}

    class Metrics:
        def __init__(self, marker):
            self.marker = marker

        async def snapshot(self):
            return {"marker": self.marker}

    first = ScheduledMemoryActivities(
        memory_reflection=Reflection("first"), metrics=Metrics("first")
    )
    second = ScheduledMemoryActivities(
        memory_reflection=Reflection("second"), metrics=Metrics("second")
    )

    assert await first.reflect("account") == {
        "marker": "first",
        "account_id": "account",
    }
    assert await second.metrics_report() == {"marker": "second"}
    assert await first.metrics_report() == {"marker": "first"}


def test_reminders_use_registered_deterministic_run_owner():
    strategy = WorkExecutionPlanner().plan({"capability_key": "reminder"})
    assert strategy.strategy_kind == "deterministic"
    assert strategy.executor_key == "reminder"
    assert ReminderActivities.__module__ == "orchestration.run_dispatcher"
    assert activity._Definition.from_callable(ReminderActivities.execute).name == (
        "execute_reminder_activity"
    )
