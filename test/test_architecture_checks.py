"""Mutation probes for the existing offline registry gate; no service access."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("temporal_registry", ROOT / "scripts/check/temporal_registry.py")
registry = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registry)


@pytest.mark.parametrize("source", [
    "from orchestration import scheduler",
    "from . import scheduler",
    "from .scheduler import run",
    "import orchestration.scheduler as old",
    "import importlib as loader\nloader.import_module('orchestration.scheduler')",
    "from importlib import import_module as load\nload('.scheduler', 'orchestration')",
    "__import__('agent_execution')",
])
def test_prohibited_import_spellings_cannot_bypass_gate(tmp_path, monkeypatch, source):
    root = tmp_path / "src/orchestration"
    root.mkdir(parents=True)
    (root / "entry.py").write_text(source)
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    definitions, imports = registry.source_violations()
    assert not definitions
    assert len(imports) == 1
    assert "src/orchestration/entry.py:" in imports[0]


def test_source_gate_accepts_current_cross_domain_calls(tmp_path, monkeypatch):
    root = tmp_path / "src/work_domain"
    root.mkdir(parents=True)
    (root / "entry.py").write_text("from workspace import resources\nfrom run_domain.results import complete_operation")
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    assert registry.source_violations() == ([], [])


def test_registration_validation_is_queue_specific_and_detects_duplicates():
    snapshots = [
        {"task_queue": "expected", "workflows": ["Capability"], "activities": ["produce", "produce"]},
        {"task_queue": "wrong", "workflows": [], "activities": ["needed"]},
    ]
    issues = registry.registration_violations(snapshots, [("expected", "Capability", "expected", {"needed"})])
    assert any(i["code"] == "duplicate_activity" and i["task_queue"] == "expected" for i in issues)
    assert any(i["code"] == "missing_activity" and i["name"] == "needed" for i in issues)


def test_live_composition_and_every_captured_workflow_pass_offline_gate():
    report = registry.scan()
    assert report["pass"], report
    assert not report["registration_violations"]
    assert not report["sandbox_violations"]
    assert report["capabilities"]
    assert report["config_checks"]["tool_policy"] == "passed"


def test_invalid_policy_cli_identifies_boundary_without_echoing_config(tmp_path, monkeypatch, capsys):
    config = tmp_path / 'config'
    config.mkdir()
    (config / 'agent_tools.yaml').write_text('schema_version: 1\nprivate_key: MUST-NOT-LEAK')
    monkeypatch.setattr(registry, 'ROOT', tmp_path)
    monkeypatch.setattr(registry, 'source_violations', lambda: ([], []))

    async def empty_workers():
        return [], {}

    monkeypatch.setattr(registry, 'capture_registries', empty_workers)
    assert registry.main() == 1
    output = capsys.readouterr().out
    assert 'config/agent_tools.yaml' in output and 'MUST-NOT-LEAK' not in output


def test_sandbox_failure_rejects_gate_with_safe_workflow_and_queue_identity(monkeypatch):
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    def reject(_self, _definition):
        raise RuntimeError('PRIVATE-CONFIGURATION')

    monkeypatch.setattr(SandboxedWorkflowRunner, 'prepare_workflow', reject)
    report = registry.scan()
    assert report['pass'] is False
    assert report['sandbox_violations']
    assert all(issue['name'] and issue['task_queue'] for issue in report['sandbox_violations'])
    assert 'PRIVATE-' not in str(report)
