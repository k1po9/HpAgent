"""W3-D proof that production has one canonical runtime/configuration surface."""
from __future__ import annotations

import ast
import asyncio
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

BASELINE = "35104a0b02b445314533e3c40d69c86cb0c7822d"
REMOVED_PATHS = (
    "config/agents.yaml",
    "scripts/merge-account.py",
    "scripts/migrate_web_credentials.py",
    "src/account/account_service.py",
    "src/account/models.py",
    "src/orchestration/web_worker.py",
)
FORBIDDEN_CONFIGURATION = (
    "DURABLE_AGENT_ENABLED",
    "WEB_REAL_AGENT_ENABLED",
    "WEB_REAL_AGENT_GATE_VERSION",
    "WEB_CREDENTIALS_JSON",
)
FORBIDDEN_DEFINITIONS = {
    "FallbackCredentialAdapter",
    "ConfiguredPasswordCredentialAdapter",
    "MultiAgentConfig",
    "AgentEntry",
}
FORBIDDEN_IMPORTS = {
    "account.account_service",
    "account.models",
    "orchestration.web_worker",
}


def production_files() -> list[Path]:
    files = list((ROOT / "src").rglob("*.py"))
    files += [ROOT / ".env.example", ROOT / "docker-compose.yaml"]
    files += [p for p in (ROOT / "scripts").glob("*") if p.is_file() and not p.name.startswith("audit_")]
    return [path for path in files if path.exists()]


def scan_imports() -> list[str]:
    violations: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                targets = [node.module]
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in {"__import__", "import_module"} and node.args:
                    value = node.args[0]
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        targets = [value.value]
            for target in targets:
                if any(target == item or target.startswith(item + ".") for item in FORBIDDEN_IMPORTS):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}:{target}")
    return sorted(violations)


def main() -> int:
    from audit_w3b_runtime import capture_registries

    from orchestration.config import AgentConfig, AppConfig, TemporalConfig
    from web_api.config import WebApiSettings

    surfaces = production_files()
    config_hits = [
        f"{path.relative_to(ROOT)}:{token}"
        for path in surfaces
        for token in FORBIDDEN_CONFIGURATION
        if token in path.read_text(errors="replace")
    ]
    definition_hits: list[str] = []
    for path in (ROOT / "src").rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in FORBIDDEN_DEFINITIONS:
                definition_hits.append(f"{path.relative_to(ROOT)}:{node.lineno}:{node.name}")

    registries, composition = asyncio.run(capture_registries())
    registered = {
        name
        for registry in registries
        for key in ("workflows", "activities")
        for name in registry[key]
    }
    required = {
        "AgentLifecycleWorkflow",
        "AgentRunWorkflow",
        "ReactAgentWorkflow",
        "PlanAndExecuteWorkflow",
        "AgentStepWorkflow",
        "ToolExecutionWorkflow",
        "ResearchReportWorkflow",
        "ResearchTaskScheduleWorkflow",
        "ArtifactBuildWorkflow",
        "NormalizeDocumentWorkflow",
        "ReflectWorkflow",
        "MetricsReportWorkflow",
    }
    report = {
        "baseline": BASELINE,
        "head_at_scan": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "removed_paths": {path: not (ROOT / path).exists() for path in REMOVED_PATHS},
        "configuration_hits": sorted(config_hits),
        "definition_hits": sorted(definition_hits),
        "import_hits": scan_imports(),
        "clean_dataclass_fields": {
            "TemporalConfig": sorted(TemporalConfig.__dataclass_fields__),
            "AgentConfig": sorted(AgentConfig.__dataclass_fields__),
            "WebApiSettings": sorted(WebApiSettings.__dataclass_fields__),
        },
        "clean_default_config_constructed": isinstance(AppConfig(), AppConfig),
        "captured_registries": registries,
        "composition": composition,
        "missing_required_registrations": sorted(required - registered),
    }
    report["pass"] = (
        all(report["removed_paths"].values())
        and not config_hits
        and not definition_hits
        and not report["import_hits"]
        and not report["missing_required_registrations"]
        and report["clean_default_config_constructed"]
    )
    target = ROOT / "artifacts/architecture-audit/phase3/W3_D_compatibility_scan.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
