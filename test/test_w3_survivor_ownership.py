"""Extraction must preserve behavior without loading retired runtime owners."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from temporalio import activity, workflow

from application.scheduler import TaskScheduler
from memory.activities import (
    inject_scheduled_services,
    metrics_report_activity,
    reflect_batch_activity,
)
from memory.maintenance import HindsightMaintenance
from memory.workflows import MetricsReportWorkflow, ReflectWorkflow


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
        }:
            raise AssertionError('canonical import reached retired owner: ' + fullname)
sys.meta_path.insert(0, Guard())
for name in (
    'main', 'web_api.app', 'orchestration.document_worker',
    'agent_activities.runtime', 'document_activities.runtime', 'research_activities.runtime',
    'memory.activities', 'memory.workflows', 'sandbox.tools.local.reminder',
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
    try:
        inject_scheduled_services(
            memory_reflection=MemoryReflectionService(maintenance),
            metrics=MetricsSnapshotService(maintenance),
        )
        assert await reflect_batch_activity(["first", "failed", "last"]) == {
            "results": {"first": 4, "failed": -1, "last": 4}, "total": 3,
        }
        assert await metrics_report_activity() == {"recall_calls": 7}
        assert activity._Definition.from_callable(reflect_batch_activity).name == "reflect_batch_activity"
        assert activity._Definition.from_callable(metrics_report_activity).name == "metrics_report_activity"
        assert workflow._Definition.from_class(ReflectWorkflow).name == "ReflectWorkflow"
        assert workflow._Definition.from_class(MetricsReportWorkflow).name == "MetricsReportWorkflow"
    finally:
        inject_scheduled_services(memory_reflection=None, metrics=None)


@pytest.mark.asyncio
async def test_reminder_tools_use_extracted_scheduler_and_persist_account_scope(tmp_path, monkeypatch):
    from sandbox.tools.local import reminder

    scheduler = TaskScheduler(tmp_path)
    monkeypatch.setattr(reminder, "_scheduler", scheduler)
    tool = reminder.create_reminder_tool({"account_id": "account-a", "channel_type": "napcat"})
    # Tool schema is the public invocation boundary, so exercise it rather than
    # constructing ScheduledTask directly.
    result = await tool.ainvoke({"content": "check report", "delay_minutes": 5})
    assert result
    restored = TaskScheduler(tmp_path)
    await restored.load()
    tasks = restored.list_by_filter(account_id="account-a")
    assert len(tasks) == 1
    assert tasks[0].params["content"] == "check report"
    assert restored.list_by_filter(account_id="account-b") == []
    assert tasks[0].__class__.__module__ == "application.scheduler"


def test_retirement_graph_has_no_unextracted_runtime_dependencies():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    try:
        from audit_w3_reachability import build_report
        rows, report = build_report()
    finally:
        sys.path.pop(0)
    assert report["forbidden_canonical_imports"] == []
    by_module = {row["module"]: row for row in rows if row["module"]}
    assert "agent.protocol" not in by_module  # W3-B removed the forwarding surface.
    assert not any(module == "session" or module.startswith("session.") for module in by_module)
    assert by_module["orchestration.run_lifecycle_contracts"]["status"] == "STILL_REACHABLE"
