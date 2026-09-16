"""W3-B retirement proof: fresh imports plus actual production registry capture.

No broker/database connections: capture Workers at the composition boundary with
inert resource dependencies. Runtime integration is validated separately.
"""
from __future__ import annotations

import ast
import asyncio
import hashlib
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from audit_w3_reachability import ROOT, ROOTS, graph, reachable, sources

sys.path.insert(0, str(ROOT / "src"))
BASELINE = "378ca723b977426b2045e293aecd622cef337e87"
RETIRED_MODULES = (
    "agent_execution", "harness", "orchestration.workflow", "orchestration.web_workflow",
    "orchestration.web_activities", "orchestration.scheduler", "agent",
)
RETIRED_TYPES = {
    "OrchestrationWorkflow", "WebRunWorkflow", "execute_agent_activity",
    "process_turn_activity", "archive_session_activity", "QQExecutionHost",
    "WebExecutionHost", "AgentExecutionFacade", "DefaultBrainActionLoop",
}


def retired(module):
    module = module.removeprefix("src.")
    return any(module == prefix or module.startswith(prefix + ".") for prefix in RETIRED_MODULES)


def import_violations(source):
    violations = []
    for module, (path, text) in source.items():
        package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
        for node in ast.walk(ast.parse(text)):
            targets = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                targets = [base, *(base + "." + alias.name for alias in node.names)]
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in {"__import__", "import_module"} and node.args:
                    arg = node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        targets = [arg.value]
            for target in targets:
                if retired(target):
                    violations.append(f"{path}:{node.lineno} -> {target}")
    return sorted(violations)


async def capture_registries():
    from temporalio import activity, workflow

    from document_activities import DocumentActivities
    from orchestration import worker
    from orchestration.config import AppConfig
    from orchestration.document_worker import build_document_worker
    from orchestration.web_workers import WEB_REAL_AGENT_GATE_VERSION

    snapshots = []

    def record(_client, **options):
        snapshots.append({
            "task_queue": options["task_queue"],
            "workflows": [workflow._Definition.from_class(cls).name for cls in options.get("workflows", [])],
            "activities": [activity._Definition.from_callable(fn).name for fn in options.get("activities", [])],
        })
        return Mock()

    config = AppConfig()
    config.temporal.web_real_agent_gate_version = WEB_REAL_AGENT_GATE_VERSION
    deps = SimpleNamespace(
        workspace_isolation=SimpleNamespace(account_locks=Mock(), close=Mock()),
        sandbox_manager=Mock(), redis_client=None, context_builder=Mock(), hindsight_client=None,
        git_repo_manager=Mock(), run_file_workspace=None, resource_pool=Mock(),
        file_output_publisher=None, tenant_file_store=Mock(), brain_engine=Mock(), action_runtime=Mock(),
        memory_reflection=Mock(), metrics=Mock(), scheduler=Mock(), mcp_manager=None,
    )
    with patch.dict(os.environ, WORKER_DATABASE_URL="postgresql://registry-only/unused"), \
            patch("orchestration.web_workers.Worker", side_effect=record):
        composition = worker.compose_web_workers(Mock(), config, deps)
    with patch("orchestration.document_worker.Worker", side_effect=record):
        build_document_worker(Mock(), SimpleNamespace(normalize_document=DocumentActivities.normalize_document))

    class RegistryCaptured(Exception):
        pass

    def stop_after_memory_registry(*args, **kwargs):
        record(*args, **kwargs)
        raise RegistryCaptured

    config.scheduler.enabled = False
    with patch.object(worker, "init_dependencies", AsyncMock(return_value=deps)), \
            patch.object(worker.Client, "connect", AsyncMock()), \
            patch.object(worker, "inject_scheduled_services"), \
            patch.object(worker, "Worker", side_effect=stop_after_memory_registry):
        try:
            await worker.start_worker(config)
        except RegistryCaptured:
            pass
    return snapshots, {
        "run_dispatcher": type(composition.dispatcher).__name__,
        "run_temporal_adapter": "TemporalClientAdapter -> AgentLifecycleWorkflow.run",
        "artifact_dispatcher": type(composition.artifact_dispatcher).__name__,
        "reminder_handlers": [call.args[0] for call in deps.scheduler.register_handler.call_args_list],
    }


def scan():
    current = sources()
    before = sources(BASELINE)
    edges, _, dynamic = graph(current)
    live = reachable(edges)
    auxiliary = {
        p.relative_to(ROOT).as_posix().removesuffix(".py").replace("/", "."):
            (p.relative_to(ROOT).as_posix(), p.read_text())
        for directory in ("test", "scripts", "tools")
        for p in (ROOT / directory).rglob("*.py")
    }
    violations = import_violations({**current, **auxiliary})
    remaining = sorted(m for m in current if retired(m))
    definitions = [
        f"{path}:{node.lineno}:{node.name}"
        for path, text in current.values() for node in ast.walk(ast.parse(text))
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in RETIRED_TYPES
    ]
    callsites = []
    for module in sorted(live):
        path, text = current[module]
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            name = getattr(node.func, "id", getattr(node.func, "attr", ""))
            if name in {"Worker", "build_web_temporal_workers", "start_workflow", "execute_child_workflow",
                        "execute_activity", "register_handler", "start_monitor", "claim"}:
                callsites.append({"path": path, "line": node.lineno, "call": ast.unparse(node)})
    registries, composition = asyncio.run(capture_registries())
    registered = {name for item in registries for field in ("workflows", "activities") for name in item[field]}
    required = {"AgentLifecycleWorkflow", "AgentRunWorkflow", "ReactAgentWorkflow", "PlanAndExecuteWorkflow",
                "AgentStepWorkflow", "ToolExecutionWorkflow", "ResearchReportWorkflow", "ResearchTaskScheduleWorkflow",
                "NormalizeDocumentWorkflow", "ArtifactBuildWorkflow", "ReflectWorkflow", "MetricsReportWorkflow"}
    forbidden = sorted(registered & RETIRED_TYPES)
    missing = sorted(required - registered)
    return {
        "baseline": BASELINE, "roots": ROOTS,
        "source_sha256": hashlib.sha256("".join(p + "\0" + text for p, text in sorted(current.values())).encode()).hexdigest(),
        "deleted_modules": sorted(m for m in before if retired(m) and m not in current),
        "retired_definitions": definitions,
        "remaining_retired_modules": remaining, "retired_import_violations": violations,
        "retired_canonical_reachability": sorted(m for m in live if retired(m)),
        "registered_retired_types": forbidden, "missing_required_workflows": missing,
        "captured_registries": registries, "composition": composition,
        "canonical_registration_callsites": callsites, "dynamic_imports": dynamic,
        "pass": not (remaining or violations or forbidden or missing or definitions),
    }


def main():
    report = scan()
    path = ROOT / "artifacts/architecture-audit/phase3/W3_B_runtime_scan.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in (
        "pass", "remaining_retired_modules", "retired_import_violations", "registered_retired_types",
        "missing_required_workflows", "captured_registries",
    )}, indent=2))
    return not report["pass"]


if __name__ == "__main__":
    raise SystemExit(main())
