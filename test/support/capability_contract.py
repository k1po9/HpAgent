"""Shared offline probes for trusted capability adapters, never a fake authority.

These drive real Workflow bodies with an Activity boundary double. Database
permissions, fencing, settlement and replay must still use the PG/Temporal suites.
"""
import importlib
from dataclasses import dataclass, field
from typing import Any

from temporalio import workflow


@dataclass
class WorkflowProbe:
    calls: list[tuple[str, Any, dict]] = field(default_factory=list)
    failure: BaseException | None = None

    async def execute(self, name, request, **options):
        self.calls.append((name, request, options))
        if self.failure is not None:
            raise self.failure
        return {'sufficient': True}

    def install(self, monkeypatch, workflow_type):
        monkeypatch.setattr(workflow, 'execute_activity', self.execute)
        monkeypatch.setattr(workflow, 'cancellation_reason', lambda: None)
        module = importlib.import_module(workflow_type.__module__)
        if hasattr(module, 'execute_segment'):
            monkeypatch.setattr(module, 'execute_segment', self.execute)
