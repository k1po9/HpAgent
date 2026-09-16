"""Rebuild W3-A module reachability from source, including package initializers.

This conservative graph includes function-local and TYPE_CHECKING imports. It
proves module import reachability, not that every imported symbol is executed.
Retirement still needs W3-B caller/test/installation review; it is not a delete tool.
"""
from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import subprocess
import tarfile
from collections import Counter, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "630ce8de2f1f72a1663a95a8f8ffa3db622d8fff"
ROOTS = (
    "main", "web_api.__main__", "web_api.app", "orchestration.web_worker",
    "orchestration.document_worker", "persistence.migrate",
)
SCOPES = ("agent_execution", "harness", "orchestration", "session", "storage", "agent")
EXTRA = (
    "application.memory", "application.session_archive", "bootstrap.qq",
    "account.account_service", "account.models", "common.types",
    "web_api.fake_executor", "web_api.app", "application.reply",
)
EXTRACTED = {
    "agent.protocol": "actions.contracts; brain.contracts",
    "agent_execution.facade": "application.execution_contracts",
    "agent_execution.chat_bindings": "conversation_domain.execution_bindings",
    "agent_execution.chat_run_input": "conversation_domain.run_input",
    "agent_execution.web_adapters": "application.chat_execution",
    "agent_execution.web_events": "web_domain.run_events",
    "agent_execution.run_budget": "resources.run_budget",
    "agent_execution.model_budget_context": "resources.model_budget_context",
    "agent_execution.activity_control": "agent_activities.control",
    "harness.context_builder": "application.context_builder",
    "harness.prompts": "application.prompts",
    "harness.activities": "memory.activities (reflection/metrics only)",
    "orchestration.workflow": "memory.workflows (ReflectWorkflow/MetricsReportWorkflow only)",
    "orchestration.scheduler": "application.scheduler",
    "bootstrap.qq": "memory.maintenance (HindsightMaintenance only)",
}
NOTES = {
    "orchestration.workflow": "Scheduled survivors extracted; conservative reachability remains only through dormant orchestration.__getattr__(OrchestrationWorkflow). W3-B must remove that export before deleting file.",
    "session.db": "Worker still constructs WorkspaceDB; retain pending composition decision (W4).",
    "session": "Eager exports import SessionStore/workspace when worker imports session.db.",
    "session.store": "Import-reachable via session.__init__; canonical QQ does not construct SessionStore. Do not infer active authority from this edge.",
    "session.workspace": "Import-reachable via session.__init__; archive helpers serve retained SessionArchiveService only, no canonical archive caller found.",
    "session.models": "WorkspaceDB and eager session exports require these models; not the PG Session DTO.",
    "application.memory": "TurnMemoryService has no canonical import/construction; legacy tests remain.",
    "application.session_archive": "Legacy archive service has no canonical caller; do not invent a new PG archive product flow in W3-A.",
    "orchestration.run_lifecycle_contracts": "Already canonical owner of queue/timeouts/FailureInput/RunLifecycleInput; retain names and wire values.",
    "orchestration.config": "Shared current config plus residual fields; file-level deletion prohibited.",
    "orchestration.worker": "Production composition and scheduled/tool registration; W4 cleanup deferred.",
    "bootstrap.qq": "Canonical QQ/shared capability composition remains; only maintenance adapter extracted.",
    "web_api.fake_executor": "Imported by API composition; fake_executor_enabled controls non-production execution. Event projection now owned by web_domain.run_events; file remains reachable.",
    "application.reply": "Canonical QQ delivery and reminder presentation capability; not a retired execution owner.",
    "common.types": "Shared Event/EventType/ChannelType/UnifiedMessage contracts remain in existing common owner.",
}


def module_name(path: str) -> str:
    value = path.removeprefix("src/").removesuffix(".py").replace("/", ".")
    return value.removesuffix(".__init__")


def sources(ref: str | None = None) -> dict[str, tuple[str, str]]:
    if ref:
        archive = subprocess.check_output(["git", "archive", ref, "src"], cwd=ROOT)
        with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
            return {
                module_name(m.name): (m.name, tar.extractfile(m).read().decode())
                for m in tar.getmembers() if m.isfile() and m.name.endswith(".py")
            }
    return {
        module_name(p.relative_to(ROOT).as_posix()): (p.relative_to(ROOT).as_posix(), p.read_text())
        for p in (ROOT / "src").rglob("*.py")
    }


def graph(source):
    edges = {m: set() for m in source}
    references = {m: [] for m in source}
    dynamic = []
    for mod, (path, text) in source.items():
        package = mod if path.endswith("/__init__.py") else mod.rpartition(".")[0]

        def add(target, line):
            parts = target.split(".")
            for size in range(1, len(parts) + 1):
                candidate = ".".join(parts[:size])
                if candidate in source and candidate != mod:
                    edges[mod].add(candidate)
                    references[candidate].append(f"{path}:{line}")

        add(package, 1)
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    add(alias.name, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    parents = package.split(".")
                    base = ".".join(parents[:len(parents) - node.level + 1] + ([base] if base else []))
                add(base, node.lineno)
                for alias in node.names:
                    add(base + "." + alias.name, node.lineno)
            elif isinstance(node, ast.Call):
                name = getattr(node.func, "id", getattr(node.func, "attr", ""))
                if name in {"import_module", "__import__"}:
                    arg = node.args[0] if node.args else None
                    literal = arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else None
                    dynamic.append({"path": path, "line": node.lineno, "target": literal})
                    if literal:
                        add(literal, node.lineno)
    return edges, references, dynamic


def reachable(edges):
    parents = {root: None for root in ROOTS if root in edges}
    queue = deque(parents)
    while queue:
        current = queue.popleft()
        for dep in sorted(edges[current]):
            if dep not in parents:
                parents[dep] = current
                queue.append(dep)
    return parents


def chain(mod, parents):
    if mod not in parents:
        return ""
    result = [mod]
    while parents[mod] is not None:
        mod = parents[mod]
        result.append(mod)
    return " -> ".join(reversed(result))


def build_report():
    before = sources(BASELINE)
    current = sources()
    old_edges, _, _ = graph(before)
    edges, refs, dynamic = graph(current)
    auxiliary = {
        p.relative_to(ROOT).as_posix().removesuffix(".py").replace("/", "."): (p.relative_to(ROOT).as_posix(), p.read_text())
        for directory in ("test", "scripts", "tools")
        for p in (ROOT / directory).rglob("*.py")
    }
    _, all_refs, _ = graph({**current, **auxiliary})
    old_live, live = reachable(old_edges), reachable(edges)
    rows = []
    for mod in sorted(current):
        if not (mod.split(".")[0] in SCOPES or mod in EXTRA):
            continue
        owner = EXTRACTED.get(mod, "")
        if mod == "agent_execution.tracing" or mod.startswith("agent_execution.tracing."):
            owner = mod.replace("agent_execution.tracing", "tracing", 1)
        status = "STILL_REACHABLE" if mod in live else "SAFE_TO_DELETE_AFTER_W3_A"
        initial = "SURVIVOR_EXTRACT_REQUIRED" if owner else (
            "STILL_REACHABLE" if mod in old_live else "SAFE_TO_DELETE_AFTER_W3_A"
        )
        note = NOTES.get(mod, "")
        if mod.startswith("storage"):
            note = "Shared storage infrastructure: actual canonical callers below; not QQ-only storage."
        if not note:
            note = "Implementation extracted; old surface retained for W3-B." if owner else (
                "Canonical import path remains; retain/review actual symbol ownership." if mod in live else
                "No canonical import path found. Retained legacy/test callers must be handled together in W3-B."
            )
        rows.append({
            "module": mod, "path": current[mod][0], "baseline_status": initial,
            "status": status, "new_owner": owner,
            "baseline_import_chain": chain(mod, old_live), "current_import_chain": chain(mod, live),
            "current_source_callers": "; ".join(sorted(set(refs[mod]))),
            "current_test_script_callers": "; ".join(sorted({r for r in all_refs[mod] if not r.startswith("src/")})),
            "reason": note,
        })
    # Not Python modules: source import reachability cannot prove operational/schema retirement.
    rows.extend([
        {"module": "", "path": "scripts/session-viewer.py", "baseline_status": "UNKNOWN",
         "status": "UNKNOWN", "reason": "Operational WAL/archive viewer; reads files rather than importing SessionStore. Operator usage cannot be proved from the runtime import graph."},
        {"module": "", "path": "scripts/merge-account.py", "baseline_status": "UNKNOWN",
         "status": "UNKNOWN", "reason": "Operational JSON rewrite script; no production module import is evidence of operator usage. Review explicitly in W3-B; never run to delete personal data."},
        {"module": "", "path": "persistence/migrations/*", "baseline_status": "STILL_REACHABLE",
         "status": "STILL_REACHABLE", "reason": "Canonical clean-schema installation chain; no migration deletion authorized."},
    ])
    blocked = [m for m in live if m.split(".")[0] in {"agent", "agent_execution", "harness"}
               or m in {"orchestration.web_workflow", "orchestration.web_activities", "orchestration.scheduler"}]
    return rows, {
        "baseline": BASELINE,
        "head_at_scan": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": hashlib.sha256("".join(p + "\0" + text for p, text in sorted(current.values())).encode()).hexdigest(),
        "roots": ROOTS, "baseline_counts": dict(Counter(row["baseline_status"] for row in rows)),
        "retained_lazy_exports": ["orchestration.OrchestrationWorkflow -> orchestration.workflow"],
        "counts": dict(Counter(row["status"] for row in rows)),
        "forbidden_canonical_imports": sorted(blocked), "dynamic_imports": dynamic,
        "reachable_modules": sorted(live),
        "semantics": "Conservative module imports including package initializers, local/type imports and literal dynamic imports. UNKNOWN blocks deletion. SAFE means canonical-unreachable candidate, not W3-B/G05 or installation approval.",
    }


def main():
    rows, report = build_report()
    out = ROOT / "artifacts/architecture-audit/phase3"
    with (out / "W3_A_retirement_map.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    (out / "W3_A_reachability.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("counts", "forbidden_canonical_imports", "source_sha256")}, indent=2))
    return bool(report["forbidden_canonical_imports"])


if __name__ == "__main__":
    raise SystemExit(main())
