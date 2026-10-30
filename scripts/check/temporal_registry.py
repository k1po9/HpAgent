#!/usr/bin/env python3
"""Validate current Temporal registrations and prohibited runtime symbols.

The check captures worker construction with inert collaborators. It opens no
database, Redis, model-provider, or Temporal connection.
"""
from __future__ import annotations

import ast
import asyncio
import os
import sys
from collections import Counter
from importlib.util import resolve_name
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PROHIBITED_MODULES = (
    "agent_execution",
    "harness",
    "orchestration.workflow",
    "orchestration.web_workflow",
    "orchestration.web_activities",
    "orchestration.scheduler",
    "agent",
)
PROHIBITED_TYPES = {
    "ArtifactBuildWorkflow",
    "OrchestrationWorkflow",
    "WebRunWorkflow",
    "execute_agent_activity",
    "process_turn_activity",
    "archive_session_activity",
    "QQExecutionHost",
    "WebExecutionHost",
    "AgentExecutionFacade",
    "DefaultBrainActionLoop",
}
REQUIRED_WORKFLOWS = {
    "AgentLifecycleWorkflow",
    "AgentRunWorkflow",
    "ReactAgentWorkflow",
    "PlanAndExecuteWorkflow",
    "AgentStepWorkflow",
    "ToolExecutionWorkflow",
    "CapabilityInvocationWorkflow",
    "WorkDelegationWorkflow",
    "NormalizeDocumentWorkflow",
    "ReflectWorkflow",
    "MetricsReportWorkflow",
}


class CheckFailure(Exception):
    """Only trusted stage identifiers cross the CLI error boundary."""

    def __init__(self, boundary: str):
        self.boundary = boundary
        super().__init__(boundary)


def checked(boundary, function):
    try:
        return function()
    except CheckFailure:
        raise
    except Exception:
        raise CheckFailure(boundary) from None


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT / "src")
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_prohibited(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in PROHIBITED_MODULES)


def import_targets(tree: ast.AST, package: str):
    """Resolve static and literal dynamic imports, including aliases and relatives.

    Computed module names are deliberately not interpreted. Sandbox preparation
    and the guarded import contract cover the actual runtime dependency closure.
    """
    loaders = {"__import__"}
    modules = {"importlib"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(a.asname or a.name for a in node.names if a.name == "importlib")
        elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
            loaders.update(a.asname or a.name for a in node.names if a.name == "import_module")
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Import):
            targets = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                base = resolve_name("." * node.level + base, package)
            targets = [base] if _is_prohibited(base) else [
                base + "." + a.name if base else a.name for a in node.names
            ]
        elif isinstance(node, ast.Call) and node.args:
            fn = node.func
            if (isinstance(fn, ast.Name) and fn.id in loaders) or (
                isinstance(fn, ast.Attribute) and fn.attr == "import_module"
                and isinstance(fn.value, ast.Name) and fn.value.id in modules
            ):
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    target = first.value
                    if target.startswith("."):
                        supplied = node.args[1] if len(node.args) > 1 else next(
                            (kw.value for kw in node.keywords if kw.arg == "package"), None
                        )
                        if isinstance(supplied, ast.Constant) and isinstance(supplied.value, str):
                            target = resolve_name(target, supplied.value)
                    targets = [target]
        for target in targets:
            yield node.lineno, target


def source_violations() -> tuple[list[str], list[str]]:
    definitions: list[str] = []
    imports: list[str] = []
    for path in sorted((ROOT / "src").rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        tree = ast.parse(text)
        module = _module_name(path)
        if _is_prohibited(module):
            definitions.append(f"prohibited module remains: {path.relative_to(ROOT)}")
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in PROHIBITED_TYPES:
                    definitions.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")
        package = module if path.name == "__init__.py" else module.rpartition(".")[0]
        for line, target in import_targets(tree, package):
            if _is_prohibited(target):
                imports.append(f"{path.relative_to(ROOT)}:{line}:{target}")
    return sorted(definitions), sorted(imports)


async def capture_registries() -> tuple[list[dict[str, object]], dict[str, object]]:
    from temporalio import activity, workflow
    from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner

    from document_activities import DocumentActivities
    from orchestration import worker
    from orchestration.config import AppConfig
    from orchestration.document_worker import build_document_worker

    snapshots: list[dict[str, object]] = []

    def record(_client, **options):
        sandbox_violations = []
        for cls in options.get("workflows", []):
            definition = workflow._Definition.must_from_class(cls)
            try:
                SandboxedWorkflowRunner().prepare_workflow(definition)
            except Exception:
                # Do not expose exception text: import errors may include config.
                sandbox_violations.append({"code": "sandbox_prepare_failed", "name": definition.name})
        snapshots.append(
            {
                "task_queue": options["task_queue"],
                "workflows": [
                    workflow._Definition.from_class(cls).name
                    for cls in options.get("workflows", [])
                ],
                "activities": [
                    activity._Definition.from_callable(fn).name
                    for fn in options.get("activities", [])
                ],
                "sandbox_violations": sandbox_violations,
            }
        )
        return Mock()

    config = AppConfig()
    deps = SimpleNamespace(
        infrastructure=SimpleNamespace(
            workspace_root=Path("/tmp/hpagent-registry-only"),
            workspace_isolation=SimpleNamespace(account_locks=Mock()),
            sandbox_manager=Mock(),
            redis_client=None,
            git_repo_manager=Mock(),
            run_file_workspace=None,
            resource_pool=Mock(),
            file_output_publisher=None,
            tenant_file_store=Mock(),
        ),
        shared=SimpleNamespace(
            context_builder=Mock(),
            prompt_loader=Mock(),
            hindsight_client=None,
            brain_engine=Mock(),
            action_runtime=Mock(),
            memory_reflection=Mock(),
            metrics=Mock(),
            account_service=Mock(),
        ),
        qq=SimpleNamespace(channel_router=Mock(), reply_service=Mock()),
        scheduler=Mock(),
        close=AsyncMock(),
    )
    with patch.dict(os.environ, WORKER_DATABASE_URL="postgresql://registry-only/unused"), patch(
        "persistence.migrate.verify_schema"
    ), patch(
        "orchestration.web_workers.Worker", side_effect=record
    ):
        composition = worker.compose_durable_runtime(Mock(), config, deps)
    with patch("orchestration.document_worker.Worker", side_effect=record):
        build_document_worker(
            Mock(), SimpleNamespace(normalize_document=DocumentActivities.normalize_document)
        )

    class RegistryCaptured(Exception):
        pass

    def stop_after_memory_registry(*args, **kwargs):
        record(*args, **kwargs)
        raise RegistryCaptured

    with patch.object(worker, "init_dependencies", AsyncMock(return_value=deps)), patch.object(
        worker.Client, "connect", AsyncMock()
    ), patch.object(worker, "Worker", side_effect=stop_after_memory_registry):
        try:
            await worker.start_worker(config)
        except RegistryCaptured:
            pass
    return snapshots, {
        "run_dispatcher": type(composition.dispatcher).__name__,
        "artifact_execution": "registered_run_strategy",
        "reminder_execution": "registered_run_strategy",
        "research_execution": "fixed_run_graph",
    }


def registration_violations(registries, requirements):
    """Check actual queue placement, keeping shared implementations legal."""
    issues = []
    available = {}
    for item in registries:
        queue = item["task_queue"]
        for field, kind in (("workflows", "workflow"), ("activities", "activity")):
            counts = Counter(item[field])
            for name, count in counts.items():
                if count > 1:
                    issues.append({"code": "duplicate_" + kind, "task_queue": queue, "name": name})
            available.setdefault((queue, field), set()).update(counts)
    for workflow_queue, name, activity_queue, activities in requirements:
        if name not in available.get((workflow_queue, "workflows"), set()):
            issues.append({"code": "missing_workflow", "task_queue": workflow_queue, "name": name})
        for name in sorted(activities - available.get((activity_queue, "activities"), set())):
            issues.append({"code": "missing_activity", "task_queue": activity_queue, "name": name})
    return issues


def catalog_checks(registries):
    from temporalio import activity

    from agent_activities.composition import capability_registrations, compose_capabilities
    from agent_workflows.contracts import AGENT_TASK_QUEUE
    from application.tool_policy import load_tool_policy

    entries = capability_registrations()
    # Reuse production validators; never parse private environment or provider config.
    policy = checked("config/agent_tools.yaml", lambda: load_tool_policy(ROOT / "config/agent_tools.yaml"))
    checked("capability_bindings", lambda: compose_capabilities(None, services=frozenset()))
    requirements, capabilities = [], []
    for entry in entries:
        names = {
            d.name for name in dir(entry.activity_type)
            if (d := activity._Definition.from_callable(getattr(entry.activity_type, name))) is not None
        }
        requirements.append((AGENT_TASK_QUEUE, entry.binding.workflow_name, entry.activity_queue, names))
        capabilities.append({
            "key": entry.definition.key, "contract_version": entry.definition.contract_version,
            "implementation_version": entry.definition.implementation_version,
            "workflow": entry.binding.workflow_name, "activity_queue": entry.activity_queue,
            "roles": [role for role in policy.roles if policy.allows(role, entry.binding.tool_name)],
        })
    return capabilities, registration_violations(registries, requirements)


def scan() -> dict[str, object]:
    definitions, imports = checked("source_imports", source_violations)
    registries, composition = checked("worker_composition", lambda: asyncio.run(capture_registries()))
    capabilities, registration_errors = checked("capability_catalog", lambda: catalog_checks(registries))
    sandbox_errors = [
        {**issue, "task_queue": item["task_queue"]}
        for item in registries for issue in item["sandbox_violations"]
    ]
    registered = {
        name
        for item in registries
        for field in ("workflows", "activities")
        for name in item[field]
    }
    prohibited_registered = sorted(registered & PROHIBITED_TYPES)
    missing = sorted(REQUIRED_WORKFLOWS - registered)
    return {
        "prohibited_definitions": definitions,
        "prohibited_imports": imports,
        "registered_prohibited_types": prohibited_registered,
        "missing_required_workflows": missing,
        "captured_registries": registries,
        "composition": composition,
        "capabilities": capabilities,
        "config_checks": {"tool_policy": "passed"},
        "registration_violations": registration_errors,
        "sandbox_violations": sandbox_errors,
        "pass": not (definitions or imports or prohibited_registered or missing or registration_errors or sandbox_errors),
    }


def main() -> int:
    import json

    try:
        report = scan()
    except CheckFailure as exc:
        report = {"pass": False, "code": "architecture_check_failed",
                  "boundary": exc.boundary, "exception_detail": "withheld"}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
