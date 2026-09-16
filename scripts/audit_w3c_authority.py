"""W3-C proof for canonical state ownership and retired QQ storage reachability."""
from __future__ import annotations

import ast
import hashlib
import inspect
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_w3b_runtime import scan as scan_runtime  # noqa: E402, I001


BASELINE = "2ca53e1676d9930778b42601bdfcb3edc5257b8e"
RETIRED_MODULES = ("session", "application.memory", "application.session_archive")
RETIRED_PATHS = (
    "scripts/session-viewer.py",
    "scripts/migrate-memory-format.py",
)
RETIRED_TYPES = {"SessionStore", "WorkspaceDB", "SessionArchiveService", "TurnMemoryService"}
RETAINED_SYSTEMS = {
    "conversation_message_session": {
        "authority": "PostgreSQL",
        "owners": ["src/conversation_domain", "src/persistence"],
    },
    "run_lifecycle_execution": {
        "authority": "PostgreSQL + Temporal",
        "owners": ["src/agent_activities", "src/agent_workflows", "src/orchestration"],
    },
    "qq_delivery": {
        "authority": "PostgreSQL qq_deliveries",
        "owners": ["src/application/qq_delivery.py", "src/conversation_domain/delivery.py"],
    },
    "redis": {
        "authority": "optional cache / online events / ambient context",
        "owners": ["src/storage/redis.py", "src/web_domain/run_events.py", "src/memory/group_context.py"],
    },
    "long_term_memory": {
        "authority": "Hindsight",
        "owners": ["src/memory/hindsight_client.py", "src/application/memory_retention.py"],
    },
    "workspace": {
        "authority": "workspace-specific files, Git and isolation state",
        "owners": ["src/workspace", "src/sandbox/git_repo.py"],
    },
    "file_document_artifact": {
        "authority": "PostgreSQL metadata + tenant/workspace object state",
        "owners": ["src/file_domain", "src/file_runtime", "src/document_activities", "src/web_artifacts"],
    },
}


def _modules() -> dict[str, tuple[str, str]]:
    result = {}
    for root_name in ("src", "test", "scripts"):
        for path in (ROOT / root_name).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            module = relative.removesuffix(".py").replace("/", ".")
            result[module] = (relative, path.read_text(encoding="utf-8"))
    return result


def _retired(module: str) -> bool:
    module = module.removeprefix("src.")
    return any(module == item or module.startswith(item + ".") for item in RETIRED_MODULES)


def scan() -> dict[str, object]:
    modules = _modules()
    imports: list[str] = []
    definitions: list[str] = []
    for module, (path, source) in modules.items():
        tree = ast.parse(source)
        package = module if path.endswith("/__init__.py") else module.rpartition(".")[0]
        for node in ast.walk(tree):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in RETIRED_TYPES:
                    definitions.append(f"{path}:{node.lineno}:{node.name}")
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parts = package.split(".")
                    base = ".".join(parts[:len(parts) - node.level + 1] + ([base] if base else []))
                targets = [base, *(f"{base}.{alias.name}" for alias in node.names)]
            for target in targets:
                if _retired(target):
                    imports.append(f"{path}:{node.lineno} -> {target}")

    missing_retained = sorted(
        owner
        for state in RETAINED_SYSTEMS.values()
        for owner in state["owners"]
        if not (ROOT / owner).exists()
    )
    runtime = scan_runtime()
    from actions.runtime import ActionRuntime
    from bootstrap.qq import build_qq_runtime
    from orchestration.config import AgentConfig, AppConfig, WorkspaceConfig
    from orchestration.worker import WorkerDependencies

    forbidden_config = sorted(
        field
        for field, fields in {
            "AppConfig.session": AppConfig.__dataclass_fields__,
            "WorkspaceConfig.db_path": WorkspaceConfig.__dataclass_fields__,
            "AgentConfig.wal_enabled": AgentConfig.__dataclass_fields__,
            "AgentConfig.checkpoint_interval": AgentConfig.__dataclass_fields__,
            "WorkerDependencies.workspace_db": WorkerDependencies.__dataclass_fields__,
            "WorkerDependencies.file_store": WorkerDependencies.__dataclass_fields__,
            "ActionRuntime.session_store": inspect.signature(ActionRuntime).parameters,
            "build_qq_runtime.file_store": inspect.signature(build_qq_runtime).parameters,
        }.items()
        if field.rpartition(".")[2] in fields
    )
    retired_paths_remaining = sorted(path for path in RETIRED_PATHS if (ROOT / path).exists())
    retired_modules_remaining = sorted(
        path for module, (path, _source) in modules.items() if _retired(module)
    )
    passed = not any((
        imports, definitions, missing_retained, forbidden_config,
        retired_paths_remaining, retired_modules_remaining,
    )) and bool(runtime["pass"])
    return {
        "baseline": BASELINE,
        "source_sha256": hashlib.sha256(
            "".join(f"{path}\0{source}" for path, source in sorted(modules.values())).encode()
        ).hexdigest(),
        "authority_map": RETAINED_SYSTEMS,
        "retired_modules_remaining": retired_modules_remaining,
        "retired_paths_remaining": retired_paths_remaining,
        "retired_imports": sorted(imports),
        "retired_definitions": sorted(definitions),
        "forbidden_config_or_composition": forbidden_config,
        "missing_retained_systems": missing_retained,
        "runtime_registry_pass": runtime["pass"],
        "captured_registries": runtime["captured_registries"],
        "pass": passed,
    }


def main() -> int:
    result = scan()
    output = ROOT / "artifacts/architecture-audit/phase3/W3_C_authority_scan.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        key: result[key] for key in (
            "pass", "retired_modules_remaining", "retired_paths_remaining",
            "retired_imports", "retired_definitions", "forbidden_config_or_composition",
            "missing_retained_systems", "runtime_registry_pass",
        )
    }, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
