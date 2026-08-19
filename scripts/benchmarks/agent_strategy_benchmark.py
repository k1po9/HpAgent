#!/usr/bin/env python3
"""Run the real HpAgent ReAct vs Plan-and-Execute benchmark.

This runner is intentionally inert unless ``--execute-model-api`` is passed. It
uses the public Web API for submission, the real durable Worker for execution,
and PostgreSQL only for read-only operation metrics.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import psycopg
import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "scripts" / "benchmarks" / "agent_strategy_tasks.yaml"
DEFAULT_RAW = ROOT / "artifacts" / "benchmarks" / "agent_strategy_trials.jsonl"
TERMINAL = {"completed", "failed", "cancelled"}
STRATEGIES = ("react", "plan_and_execute")


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def git_value(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    return result.stdout.strip() or "unavailable"


def load_manifest(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        manifest = yaml.safe_load(stream)
    if not isinstance(manifest, dict) or not isinstance(manifest.get("tasks"), list):
        raise ValueError("manifest must contain a tasks list")
    ids = [str(task.get("id", "")) for task in manifest["tasks"]]
    levels = Counter(str(task.get("level", "")) for task in manifest["tasks"])
    if len(ids) != 30 or len(set(ids)) != 30:
        raise ValueError("manifest must contain exactly 30 unique tasks")
    if levels != {"simple": 10, "medium": 10, "complex": 10}:
        raise ValueError(f"manifest must contain 10 tasks per level, got {dict(levels)}")
    for task in manifest["tasks"]:
        if not task.get("prompt") or not isinstance(task.get("files"), dict):
            raise ValueError(f"task {task.get('id')} needs prompt and files")
        if task.get("grader", {}).get("type") not in {
            "tag_equals",
            "file_equals",
            "file_contains",
            "pytest",
        }:
            raise ValueError(f"task {task.get('id')} has unsupported grader")
    return manifest


def manifest_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe_materialize(workspace: Path, execution_id: str, task: dict[str, Any]) -> Path:
    if workspace.name != "repo" or not (workspace / ".git").exists():
        raise ValueError("benchmark workspace must be an existing Git repo directory named 'repo'")
    relative = Path("agent_strategy_benchmark") / execution_id / str(task["id"])
    target = workspace / relative
    target.mkdir(parents=True, exist_ok=False)
    for relative_name, content in task["files"].items():
        destination = target / str(relative_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(str(content), encoding="utf-8")
    (target / ".gitignore").write_text(
        ".pytest_cache/\n__pycache__/\n*.pyc\n", encoding="utf-8"
    )
    return target


def git(workspace: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(workspace), *args],
        text=True,
        capture_output=True,
        check=False,
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result


def default_branch(workspace: Path) -> str:
    result = git(
        workspace,
        "for-each-ref",
        "--format=%(refname:short)",
        "refs/heads",
    )
    branches = [line for line in result.stdout.splitlines() if line]
    for preferred in ("main", "master"):
        if preferred in branches:
            return preferred
    candidates = [name for name in branches if not name.startswith("hpagent/")]
    if len(candidates) != 1:
        raise RuntimeError(f"cannot identify benchmark repo default branch: {branches}")
    return candidates[0]


def preserve_interrupted_workspace(workspace: Path) -> None:
    if not git(workspace, "status", "--porcelain").stdout.strip():
        return
    benchmark_root = "agent_strategy_benchmark"
    unstaged_outside = git(
        workspace,
        "diff",
        "--quiet",
        "--",
        ".",
        f":(exclude){benchmark_root}/**",
        check=False,
    )
    staged_outside = git(
        workspace,
        "diff",
        "--cached",
        "--quiet",
        "--",
        ".",
        f":(exclude){benchmark_root}/**",
        check=False,
    )
    if unstaged_outside.returncode not in {0, 1} or staged_outside.returncode not in {0, 1}:
        raise RuntimeError("failed to inspect benchmark repo tracked changes")
    untracked = git(
        workspace, "ls-files", "--others", "--exclude-standard", "-z"
    ).stdout.split("\0")
    untracked_outside = [
        path for path in untracked if path and not path.startswith(f"{benchmark_root}/")
    ]
    if unstaged_outside.returncode == 1 or staged_outside.returncode == 1 or untracked_outside:
        raise RuntimeError(
            "benchmark repo has changes outside the managed benchmark directory; "
            "refusing to overwrite it"
        )
    git(workspace, "add", "-A", "--", benchmark_root)
    staged = git(workspace, "diff", "--cached", "--quiet", check=False)
    if staged.returncode not in {0, 1}:
        raise RuntimeError(staged.stderr.strip() or "git staged-state check failed")
    if staged.returncode == 1:
        git(workspace, "commit", "-m", "benchmark: preserve interrupted workspace")
    if git(workspace, "status", "--porcelain").stdout.strip():
        raise RuntimeError("benchmark repo remained dirty after interruption recovery")


def prepare_session_branch(workspace: Path, session_id: str) -> str:
    preserve_interrupted_workspace(workspace)
    base = default_branch(workspace)
    git(workspace, "checkout", base)
    branch = f"hpagent/{session_id}"
    exists = git(workspace, "branch", "--list", branch).stdout.strip()
    if exists:
        git(workspace, "checkout", branch)
    else:
        git(workspace, "checkout", "-b", branch)
    return base


def finalize_session_branch(workspace: Path, base: str, task_dir: Path) -> list[str]:
    relative = task_dir.relative_to(workspace)
    git(workspace, "add", "--", str(relative))
    staged = git(workspace, "diff", "--cached", "--quiet", check=False)
    if staged.returncode not in {0, 1}:
        raise RuntimeError(staged.stderr.strip() or "git staged-state check failed")
    if staged.returncode == 1:
        git(workspace, "commit", "-m", f"benchmark:{task_dir.name}")
    changed = git(workspace, "diff", "--name-only", f"{base}...HEAD").stdout.splitlines()
    changed.extend(git(workspace, "diff", "--name-only").stdout.splitlines())
    changed.extend(
        name
        for name in git(
            workspace, "ls-files", "--others", "--exclude-standard", "-z"
        ).stdout.split("\0")
        if name
    )
    prefix = f"{relative.as_posix()}/"
    return sorted(
        {
            name
            for name in changed
            if name != relative.as_posix() and not name.startswith(prefix)
        }
    )


def ensure_conversation_session(
    database_url: str, account_id: str, conversation_id: str
) -> str:
    from web_domain.sessions import ConversationSessionService

    service = ConversationSessionService(database_url)
    return str(service.get_or_create_active(UUID(account_id), UUID(conversation_id)))


def file_hashes(root: Path) -> dict[str, str]:
    ignored = {".pytest_cache", "__pycache__"}
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in ignored for part in path.parts):
            continue
        result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def grade(task: dict[str, Any], task_dir: Path, answer: str) -> tuple[bool, str | None]:
    grader = task["grader"]
    kind = grader["type"]
    if kind == "tag_equals":
        match = re.search(r"<answer>\s*(.*?)\s*</answer>", answer, re.DOTALL | re.IGNORECASE)
        if not match:
            return False, "missing_answer_tag"
        actual = " ".join(match.group(1).split())
        expected = " ".join(str(grader["value"]).split())
        return actual == expected, None if actual == expected else f"expected={expected!r}, actual={actual!r}"
    path = task_dir / str(grader["path"])
    if kind in {"file_equals", "file_contains"}:
        if not path.is_file():
            return False, f"missing_file:{grader['path']}"
        content = path.read_text(encoding="utf-8")
        if kind == "file_equals":
            ok = content == str(grader["value"])
            return ok, None if ok else "file_content_mismatch"
        missing = [value for value in grader["contains"] if str(value) not in content]
        return not missing, None if not missing else f"missing_content:{missing}"
    command = [sys.executable, "-m", "pytest", "-q", str(grader["path"])]
    result = subprocess.run(
        command, cwd=task_dir, text=True, capture_output=True, timeout=60, check=False
    )
    detail = (result.stdout + "\n" + result.stderr)[-4000:]
    return result.returncode == 0, None if result.returncode == 0 else detail


class HpAgentClient:
    def __init__(self, base_url: str, origin: str, username: str, password: str) -> None:
        self.origin = origin.rstrip("/")
        self.client = httpx.Client(base_url=base_url.rstrip("/"), timeout=30, follow_redirects=False)
        response = self.client.post(
            "/auth/login",
            json={"username": username, "password": password, "return_to": "/"},
        )
        if response.status_code != 303:
            raise RuntimeError(f"login failed: HTTP {response.status_code} {response.text[:500]}")
        me = self.client.get("/api/v1/me")
        me.raise_for_status()
        payload = me.json()
        if not payload.get("capabilities", {}).get("durable_agent"):
            raise RuntimeError("server reports durable_agent=false")
        if set(payload["capabilities"].get("agent_strategies", [])) != set(STRATEGIES):
            raise RuntimeError("server does not expose both benchmark strategies")
        self.account_id = str(payload["account"]["account_id"])
        self.csrf = str(payload["csrf_token"])

    def headers(self) -> dict[str, str]:
        return {
            "Origin": self.origin,
            "X-CSRF-Token": self.csrf,
            "Idempotency-Key": str(uuid4()),
        }

    def create_conversation(self, title: str) -> str:
        response = self.client.post(
            "/api/v1/conversations", json={"title": title}, headers=self.headers()
        )
        if response.status_code != 201:
            raise RuntimeError(f"conversation creation failed: HTTP {response.status_code} {response.text[:500]}")
        return str(response.json()["conversation"]["conversation_id"])

    def submit(self, conversation_id: str, prompt: str, strategy: str) -> dict[str, Any]:
        response = self.client.post(
            f"/api/v1/conversations/{conversation_id}/messages",
            json={"content": prompt, "agent_strategy": strategy},
            headers=self.headers(),
        )
        if response.status_code != 202:
            raise RuntimeError(f"message submission failed: HTTP {response.status_code} {response.text[:500]}")
        return dict(response.json()["run"])

    def wait(self, run_id: str, timeout_seconds: int, poll_seconds: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = self.client.get(f"/api/v1/runs/{run_id}")
            response.raise_for_status()
            run = dict(response.json()["run"])
            if run["status"] in TERMINAL:
                return run
            time.sleep(poll_seconds)
        raise TimeoutError(f"run {run_id} did not finish within {timeout_seconds}s")

    def answer(self, conversation_id: str, run_id: str) -> str:
        response = self.client.get(
            f"/api/v1/conversations/{conversation_id}/messages", params={"limit": 100}
        )
        response.raise_for_status()
        messages = response.json().get("items", [])
        selected = [item for item in messages if item.get("produced_by_run_id") == run_id]
        return str(selected[-1].get("content") or "") if selected else ""


def operation_metrics(database_url: str, run_id: str) -> dict[str, Any]:
    with psycopg.connect(database_url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT operation_type,status,attempt_count,error_code FROM agent_operations "
            "WHERE run_id=%s ORDER BY started_at,operation_id",
            (run_id,),
        )
        rows = cursor.fetchall()
    counts = Counter(str(row[0]) for row in rows)
    # Both plan creation and plan evaluation call BrainEngine; they are model
    # invocations even though their durable operation_type is ``planning``.
    model_calls = counts["model"] + counts["synthesis"] + counts["planning"]
    return {
        "model_calls": model_calls,
        "tool_calls": counts["tool"],
        "planning_calls": counts["planning"],
        "turns": model_calls,
        "operation_attempts": sum(int(row[2]) for row in rows),
        "operation_failures": sum(row[1] in {"failed", "uncertain"} for row in rows),
        "operation_error_codes": sorted({str(row[3]) for row in rows if row[3]}),
    }


def classify_failure(run: dict[str, Any], grade_reason: str | None, error: BaseException | None) -> tuple[str | None, str | None]:
    if error is not None:
        if isinstance(error, TimeoutError):
            return "timeout", str(error)
        return "environment_error", f"{type(error).__name__}: {error}"
    if run.get("status") != "completed":
        failure = run.get("failure") or {}
        code = str(failure.get("code") or "execution_error")
        mapping = {
            "model_unavailable": "model_error",
            "tool_execution_failed": "execution_error",
            "max_turns_exceeded": "max_turns",
            "planning_failed": "planning_error",
        }
        return mapping.get(code, "execution_error"), f"{code}: {failure.get('message', '')}"
    if grade_reason:
        return "incorrect_result", grade_reason
    return None, None


def append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def completed_keys(path: Path) -> set[tuple[str, str, int]]:
    if not path.exists():
        return set()
    keys: set[tuple[str, str, int]] = set()
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                row = json.loads(line)
                if row.get("failure_type") == "environment_error" and row.get("run_status") is None:
                    continue
                keys.add((str(row["task_id"]), str(row["strategy"]), int(row["repetition"])))
    return keys


def environment_record(args: argparse.Namespace, digest: str) -> dict[str, Any]:
    return {
        "git_commit": git_value("rev-parse", "HEAD"),
        "branch": git_value("branch", "--show-current"),
        "timestamp": utc_now(),
        "python_version": platform.python_version(),
        "host_os": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        "manifest_sha256": digest,
        "model": args.model,
        "provider": args.provider,
        "temperature": args.temperature,
        "max_turns": args.max_turns,
        "repetitions": args.repetitions,
        "token_usage": "unavailable",
    }


def preflight(args: argparse.Namespace) -> None:
    manifest = load_manifest(args.manifest)
    print(json.dumps({
        "ready": True,
        "tasks": len(manifest["tasks"]),
        "levels": dict(Counter(task["level"] for task in manifest["tasks"])),
        "manifest_sha256": manifest_digest(args.manifest),
        "will_call_model_api": False,
        "required_environment": [
            "HPAGENT_BENCHMARK_BASE_URL",
            "HPAGENT_BENCHMARK_ORIGIN",
            "HPAGENT_BENCHMARK_USERNAME",
            "HPAGENT_BENCHMARK_PASSWORD",
            "HPAGENT_BENCHMARK_WORKSPACE",
            "APP_DATABASE_URL",
            "WORKER_DATABASE_URL",
        ],
    }, ensure_ascii=False, indent=2))


def run(args: argparse.Namespace) -> None:
    if not args.execute_model_api:
        raise SystemExit("Refusing to call the model API without --execute-model-api")
    manifest = load_manifest(args.manifest)
    known_task_ids = {str(task["id"]) for task in manifest["tasks"]}
    unknown_task_ids = sorted(set(args.task_ids or []) - known_task_ids)
    if unknown_task_ids:
        raise SystemExit(f"unknown --task-ids: {', '.join(unknown_task_ids)}")
    required = {
        name: os.getenv(name, "").strip()
        for name in (
            "HPAGENT_BENCHMARK_BASE_URL",
            "HPAGENT_BENCHMARK_ORIGIN",
            "HPAGENT_BENCHMARK_USERNAME",
            "HPAGENT_BENCHMARK_PASSWORD",
            "HPAGENT_BENCHMARK_WORKSPACE",
            "APP_DATABASE_URL",
            "WORKER_DATABASE_URL",
        )
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit(f"missing required environment: {', '.join(missing)}")
    workspace = Path(required["HPAGENT_BENCHMARK_WORKSPACE"]).resolve()
    client = HpAgentClient(
        required["HPAGENT_BENCHMARK_BASE_URL"],
        required["HPAGENT_BENCHMARK_ORIGIN"],
        required["HPAGENT_BENCHMARK_USERNAME"],
        required["HPAGENT_BENCHMARK_PASSWORD"],
    )
    if workspace.parent.name != client.account_id:
        raise SystemExit("benchmark workspace account directory does not match authenticated account_id")
    run_group = args.run_group or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    prior = completed_keys(args.output) if args.resume else set()
    env = environment_record(args, manifest_digest(args.manifest))
    selected_levels = set(args.levels)
    selected_strategies = tuple(args.strategies)
    selected_tasks = set(args.task_ids or [])
    for task in manifest["tasks"]:
        if task["level"] not in selected_levels:
            continue
        if selected_tasks and task["id"] not in selected_tasks:
            continue
        for repetition in range(1, args.repetitions + 1):
            for strategy in selected_strategies:
                key = (str(task["id"]), strategy, repetition)
                if key in prior:
                    print(f"SKIP {key}: already recorded")
                    continue
                execution_id = f"{run_group}-{task['id']}-{strategy}-r{repetition}-{uuid4().hex[:8]}"
                started_at = utc_now()
                started = time.monotonic()
                run_payload: dict[str, Any] = {}
                answer = ""
                error: BaseException | None = None
                interrupted = False
                grade_reason: str | None = None
                success = False
                metrics = {key: None for key in ("model_calls", "tool_calls", "planning_calls", "turns", "operation_attempts", "operation_failures", "operation_error_codes")}
                conversation_id = run_id = workflow_id = None
                task_dir: Path | None = None
                relative_dir: Path | None = None
                before: dict[str, str] = {}
                base_branch: str | None = None
                try:
                    conversation_id = client.create_conversation(f"Agent benchmark {task['id']} {strategy} r{repetition}")
                    session_id = ensure_conversation_session(
                        required["APP_DATABASE_URL"],
                        client.account_id,
                        conversation_id,
                    )
                    base_branch = prepare_session_branch(workspace, session_id)
                    task_dir = safe_materialize(workspace, execution_id, task)
                    before = file_hashes(task_dir)
                    relative_dir = task_dir.relative_to(workspace)
                    prompt = (
                        f"这是策略对比基准任务。只操作目录 `{relative_dir.as_posix()}`，不得修改其外文件。\n"
                        f"{task['prompt']}\n"
                        "完成后按任务要求返回结果；不要请求人工确认。"
                    )
                    started_at = utc_now()
                    started = time.monotonic()
                    accepted = client.submit(conversation_id, prompt, strategy)
                    run_id = str(accepted["run_id"])
                    run_payload = client.wait(run_id, args.timeout_seconds, args.poll_seconds)
                    workflow_id = f"web-run-{run_id}"
                    answer = client.answer(conversation_id, run_id)
                    if run_payload.get("status") == "completed":
                        success, grade_reason = grade(task, task_dir, answer)
                    metrics = operation_metrics(required["WORKER_DATABASE_URL"], run_id)
                except KeyboardInterrupt as exc:
                    error = exc
                    interrupted = True
                except Exception as exc:  # preserve every failed trial before continuing
                    error = exc
                failure_type, failure_reason = classify_failure(run_payload, grade_reason, error)
                after = file_hashes(task_dir) if task_dir else {}
                allowed = set(task.get("allowed_changes", []))
                forbidden_changes = sorted(
                    name for name in set(before) | set(after)
                    if before.get(name) != after.get(name) and name not in allowed
                )
                if forbidden_changes:
                    success = False
                    failure_type, failure_reason = "forbidden_file_change", ",".join(forbidden_changes)
                repo_scope_changes: list[str] = []
                if task_dir is not None and base_branch is not None:
                    try:
                        repo_scope_changes = finalize_session_branch(
                            workspace, base_branch, task_dir
                        )
                    except Exception as exc:
                        success = False
                        failure_type = "environment_error"
                        failure_reason = f"workspace finalization failed: {exc}"
                if repo_scope_changes:
                    success = False
                    failure_type = "forbidden_file_change"
                    failure_reason = f"outside task directory: {repo_scope_changes}"
                record = {
                    "experiment": "agent_strategy",
                    "run_group": run_group,
                    "task_id": task["id"],
                    "level": task["level"],
                    "strategy": strategy,
                    "repetition": repetition,
                    "success": bool(success and run_payload.get("status") == "completed"),
                    "latency_ms": round((time.monotonic() - started) * 1000, 3),
                    **metrics,
                    "input_tokens": None,
                    "output_tokens": None,
                    "total_tokens": None,
                    "failure_type": failure_type,
                    "failure_reason": failure_reason,
                    "run_id": run_id,
                    "workflow_id": workflow_id,
                    "conversation_id": conversation_id,
                    "run_status": run_payload.get("status"),
                    "assistant_answer": answer,
                    "workspace_relative_path": str(relative_dir or ""),
                    "forbidden_changes": forbidden_changes,
                    "repo_scope_changes": repo_scope_changes,
                    "started_at": started_at,
                    "completed_at": utc_now(),
                    "environment": env,
                }
                append_record(args.output, record)
                print(
                    f"{task['id']} {strategy} r{repetition}: "
                    f"{'PASS' if record['success'] else 'FAIL'} "
                    f"({record['latency_ms'] / 1000:.1f}s)",
                    flush=True,
                )
                if interrupted:
                    raise KeyboardInterrupt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    check = sub.add_parser("preflight", help="validate the task pack without network/API calls")
    check.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    execute = sub.add_parser("run", help="execute real model trials")
    execute.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    execute.add_argument("--output", type=Path, default=DEFAULT_RAW)
    execute.add_argument("--execute-model-api", action="store_true")
    execute.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    execute.add_argument("--run-group")
    execute.add_argument("--repetitions", type=int, choices=(1, 2, 3), default=1)
    execute.add_argument("--levels", nargs="+", choices=("simple", "medium", "complex"), default=["simple", "medium", "complex"])
    execute.add_argument("--task-ids", nargs="+", help="optional exact task IDs for a pilot")
    execute.add_argument("--strategies", nargs="+", choices=STRATEGIES, default=list(STRATEGIES))
    execute.add_argument("--timeout-seconds", type=int, default=900)
    execute.add_argument("--poll-seconds", type=float, default=2.0)
    execute.add_argument("--model", required=True)
    execute.add_argument("--provider", required=True)
    execute.add_argument(
        "--temperature",
        required=True,
        help="effective value, or 'provider-default' when the Worker sends none",
    )
    execute.add_argument("--max-turns", type=int, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    if args.command == "preflight":
        preflight(args)
    else:
        run(args)


if __name__ == "__main__":
    main()
