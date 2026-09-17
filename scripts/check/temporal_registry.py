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
    "ResearchReportWorkflow",
    "ResearchTaskScheduleWorkflow",
    "NormalizeDocumentWorkflow",
    "ArtifactBuildWorkflow",
    "ReflectWorkflow",
    "MetricsReportWorkflow",
}


def _module_name(path: Path) -> str:
    relative = path.relative_to(ROOT / "src")
    parts = list(relative.with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_prohibited(module: str) -> bool:
    return any(module == prefix or module.startswith(prefix + ".") for prefix in PROHIBITED_MODULES)


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
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            for target in targets:
                if _is_prohibited(target):
                    imports.append(f"{path.relative_to(ROOT)}:{node.lineno}:{target}")
    return sorted(definitions), sorted(imports)


async def capture_registries() -> tuple[list[dict[str, object]], dict[str, object]]:
    from temporalio import activity, workflow

    from document_activities import DocumentActivities
    from orchestration import worker
    from orchestration.config import AppConfig
    from orchestration.document_worker import build_document_worker

    snapshots: list[dict[str, object]] = []

    def record(_client, **options):
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
            }
        )
        return Mock()

    config = AppConfig()
    deps = SimpleNamespace(
        infrastructure=SimpleNamespace(
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

    config.scheduler.enabled = False
    with patch.object(worker, "init_dependencies", AsyncMock(return_value=deps)), patch.object(
        worker.Client, "connect", AsyncMock()
    ), patch.object(worker, "Worker", side_effect=stop_after_memory_registry):
        try:
            await worker.start_worker(config)
        except RegistryCaptured:
            pass
    return snapshots, {
        "run_dispatcher": type(composition.dispatcher).__name__,
        "artifact_dispatcher": type(composition.artifact_dispatcher).__name__,
        "reminder_handlers": [call.args[0] for call in deps.scheduler.register_handler.call_args_list],
    }


def scan() -> dict[str, object]:
    definitions, imports = source_violations()
    registries, composition = asyncio.run(capture_registries())
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
        "pass": not (definitions or imports or prohibited_registered or missing),
    }


def main() -> int:
    import json

    report = scan()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
