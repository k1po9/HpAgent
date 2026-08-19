#!/usr/bin/env python3
"""Run repeatable real-process Temporal Workflow Worker recovery trials."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

from temporalio.client import Client

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.contracts import AGENT_SCHEMA_VERSION, AGENT_TASK_QUEUE, AgentRunInput

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "test" / "support" / "durable_worker_process.py"


@dataclass(frozen=True)
class Case:
    case_id: str
    strategy: str
    fault_boundary: str
    expected_operations: dict[str, int]
    expected_transcript_version: int
    expected_side_effects: int


CASES = (
    Case(
        "T1",
        "react",
        "react-model-decision",
        {"context": 1, "model": 2, "tool": 1},
        4,
        1,
    ),
    Case(
        "T2",
        "react",
        "react-tool-before-side-effect",
        {"context": 1, "model": 2, "tool": 1},
        4,
        1,
    ),
    Case(
        "T3",
        "react",
        "react-tool-after-side-effect-before-ack",
        {"context": 1, "model": 2, "tool": 1},
        4,
        1,
    ),
    Case(
        "T4",
        "plan_and_execute",
        "plan-step-2",
        {"context": 1, "planning": 1, "model": 3, "evaluation": 2},
        5,
        0,
    ),
    Case(
        "T5",
        "plan_and_execute",
        "plan-final-evaluation",
        {"context": 1, "planning": 1, "model": 3, "evaluation": 2},
        5,
        0,
    ),
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def request_for(strategy: str) -> AgentRunInput:
    run_id = str(uuid4())
    return AgentRunInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        strategy,
        str(uuid4()),
        1,
        "web_plan" if strategy == "plan_and_execute" else "web_chat",
        3,
    )


async def wait_for_path(path: Path, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.05)


async def start_worker(
    role: str,
    host: str,
    namespace: str,
    state_dir: Path,
    ready_name: str,
    fault_boundary: str,
    timeout: float,
) -> asyncio.subprocess.Process:
    environment = os.environ.copy()
    source_path = str(ROOT / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_path, environment.get("PYTHONPATH", "")) if item
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(HARNESS),
        role,
        host,
        namespace,
        str(state_dir),
        ready_name,
        "--fault-boundary",
        fault_boundary,
        env=environment,
    )
    try:
        await wait_for_path(state_dir / f"{ready_name}.ready", timeout)
    except BaseException:
        await terminate(process)
        raise
    if process.returncode is not None:
        raise RuntimeError(f"{role} Worker exited during startup: {process.returncode}")
    return process


async def terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)


def counts(state_dir: Path) -> tuple[dict[str, int], dict[str, int], int]:
    database = state_dir / "operations.sqlite"
    if not database.exists():
        return {}, {}, 0
    with sqlite3.connect(database) as connection:
        operations = dict(
            connection.execute("SELECT kind, count(*) FROM operations GROUP BY kind")
        )
        attempts = dict(
            connection.execute("SELECT kind, count(*) FROM attempts GROUP BY kind")
        )
        side_effects = connection.execute("SELECT count(*) FROM side_effects").fetchone()[0]
    return operations, attempts, int(side_effects)


def duplicate_count(operations: dict[str, int], attempts: dict[str, int]) -> int:
    return sum(
        max(attempts.get(kind, 0) - operations.get(kind, 0), 0)
        for kind in set(operations) | set(attempts)
    )


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return round(ordered[lower] * (1 - weight) + ordered[upper] * weight, 3)


def command_output(command: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout.strip() or completed.stderr.strip() or None


def environment(config: dict[str, Any]) -> dict[str, Any]:
    try:
        temporal_sdk = version("temporalio")
    except PackageNotFoundError:
        temporal_sdk = "unavailable"
    memory_bytes: int | None = None
    try:
        memory_bytes = int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    except (AttributeError, OSError, ValueError):
        pass
    return {
        "git_commit": command_output(["git", "rev-parse", "HEAD"]),
        "branch": command_output(["git", "branch", "--show-current"]),
        "timestamp": utc_now(),
        "python_version": platform.python_version(),
        "temporal_sdk_version": temporal_sdk,
        "docker_compose_version": command_output(["docker", "compose", "version", "--short"]),
        "host_os": platform.platform(),
        "cpu": platform.processor() or None,
        "logical_cpu_count": os.cpu_count(),
        "memory_bytes": memory_bytes,
        "benchmark_config": config,
    }


async def run_trial(
    client: Client,
    host: str,
    namespace: str,
    case: Case,
    index: int,
    runs_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    trial_id = f"{case.case_id.lower()}-{index:02d}-{uuid4().hex[:8]}"
    state_dir = runs_dir / trial_id
    state_dir.mkdir(parents=True, exist_ok=False)
    request = request_for(case.strategy)
    workflow_id = f"temporal-recovery-{request.run_id}"
    record: dict[str, Any] = {
        "experiment": "temporal_recovery",
        "trial_id": trial_id,
        "case_id": case.case_id,
        "strategy": case.strategy,
        "fault_boundary": case.fault_boundary,
        "workflow_id": workflow_id,
        "run_id": None,
        "agent_run_id": request.run_id,
        "started_at": utc_now(),
        "worker_killed_at": None,
        "replacement_started_at": None,
        "completed_at": None,
        "workflow_completed": False,
        "recovery_success": False,
        "operation_count_expected": sum(case.expected_operations.values()),
        "operation_count_actual": 0,
        "duplicate_operation_count": 0,
        "side_effect_count_expected": case.expected_side_effects,
        "side_effect_count_actual": 0,
        "duplicate_side_effect_count": 0,
        "recovery_latency_ms": None,
        "error_type": None,
        "error_message": None,
        "state_dir": display_path(state_dir),
    }
    activity_worker = workflow_worker = replacement_worker = None
    kill_monotonic: float | None = None
    try:
        activity_worker = await start_worker(
            "activity", host, namespace, state_dir, "activity", case.fault_boundary, timeout
        )
        workflow_worker = await start_worker(
            "workflow", host, namespace, state_dir, "workflow-1", case.fault_boundary, timeout
        )
        handle = await client.start_workflow(
            AgentRunWorkflow.run,
            request,
            id=workflow_id,
            task_queue=AGENT_TASK_QUEUE,
        )
        record["run_id"] = getattr(handle, "first_execution_run_id", None)
        await wait_for_path(state_dir / f"{case.fault_boundary}.boundary", timeout)
        record["worker_killed_at"] = utc_now()
        kill_monotonic = time.monotonic()
        await terminate(workflow_worker)
        (state_dir / f"{case.fault_boundary}.release").touch()
        replacement_worker = await start_worker(
            "workflow", host, namespace, state_dir, "workflow-2", case.fault_boundary, timeout
        )
        record["replacement_started_at"] = utc_now()
        result = await asyncio.wait_for(handle.result(), timeout=timeout)
        record["completed_at"] = utc_now()
        record["workflow_completed"] = True
        record["recovery_latency_ms"] = round((time.monotonic() - kill_monotonic) * 1000, 3)
        operations, attempts, side_effects = counts(state_dir)
        duplicates = duplicate_count(operations, attempts)
        record["operation_count_actual"] = sum(operations.values())
        record["duplicate_operation_count"] = duplicates
        record["side_effect_count_actual"] = side_effects
        record["duplicate_side_effect_count"] = max(
            side_effects - case.expected_side_effects, 0
        )
        checks = {
            "transcript_version": result.transcript_version == case.expected_transcript_version,
            "operations": operations == case.expected_operations,
            "operation_attempts": attempts == case.expected_operations,
            "side_effects": side_effects == case.expected_side_effects,
        }
        record["recovery_success"] = all(checks.values())
        if not record["recovery_success"]:
            record["error_type"] = "InvariantViolation"
            record["error_message"] = json.dumps(
                {"checks": checks, "operations": operations, "attempts": attempts},
                ensure_ascii=False,
                sort_keys=True,
            )
    except Exception as exc:
        record["completed_at"] = utc_now()
        if kill_monotonic is not None:
            record["recovery_latency_ms"] = round(
                (time.monotonic() - kill_monotonic) * 1000, 3
            )
        operations, attempts, side_effects = counts(state_dir)
        record["operation_count_actual"] = sum(operations.values())
        record["duplicate_operation_count"] = duplicate_count(operations, attempts)
        record["side_effect_count_actual"] = side_effects
        record["duplicate_side_effect_count"] = max(
            side_effects - case.expected_side_effects, 0
        )
        record["error_type"] = type(exc).__name__
        record["error_message"] = str(exc)[:2000]
    finally:
        await terminate(replacement_worker)
        await terminate(workflow_worker)
        await terminate(activity_worker)
    return record


def summarize(trials: list[dict[str, Any]], env: dict[str, Any]) -> dict[str, Any]:
    successes = [trial for trial in trials if trial["recovery_success"]]
    latencies = [
        float(trial["recovery_latency_ms"])
        for trial in successes
        if trial["recovery_latency_ms"] is not None
    ]
    side_effect_trials = [trial for trial in trials if trial["side_effect_count_expected"] > 0]

    def group(case_id: str) -> dict[str, Any]:
        selected = [trial for trial in trials if trial["case_id"] == case_id]
        selected_successes = [trial for trial in selected if trial["recovery_success"]]
        selected_latencies = [
            float(trial["recovery_latency_ms"])
            for trial in selected_successes
            if trial["recovery_latency_ms"] is not None
        ]
        return {
            "trials": len(selected),
            "recovery_success": len(selected_successes),
            "recovery_success_rate": len(selected_successes) / len(selected) if selected else None,
            "recovery_latency_ms": {
                "p50": percentile(selected_latencies, 0.5),
                "p95": percentile(selected_latencies, 0.95),
            },
        }

    return {
        "experiment": "temporal_recovery",
        "environment": env,
        "definitions": {
            "recovery_success_rate": "successful invariant-checked workflow recoveries / all injected-fault trials",
            "duplicate_operation_rate": "trials with operation attempts exceeding unique operation ids / all trials",
            "duplicate_side_effect_rate": "trials with side effects exceeding the expected count / trials that execute side effects",
            "recovery_latency_ms": "monotonic elapsed time from SIGKILL request until workflow result",
            "percentile_method": "linear interpolation at (n-1)*q over successful trials",
        },
        "trials": len(trials),
        "recovery_success": len(successes),
        "recovery_success_rate": len(successes) / len(trials) if trials else None,
        "duplicate_operation_trials": sum(
            trial["duplicate_operation_count"] > 0 for trial in trials
        ),
        "duplicate_operation_rate": (
            sum(trial["duplicate_operation_count"] > 0 for trial in trials) / len(trials)
            if trials
            else None
        ),
        "duplicate_side_effect_trials": sum(
            trial["duplicate_side_effect_count"] > 0 for trial in side_effect_trials
        ),
        "duplicate_side_effect_rate": (
            sum(trial["duplicate_side_effect_count"] > 0 for trial in side_effect_trials)
            / len(side_effect_trials)
            if side_effect_trials
            else None
        ),
        "recovery_latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "cases": {case.case_id: group(case.case_id) for case in CASES},
        "failures": [
            {
                "trial_id": trial["trial_id"],
                "case_id": trial["case_id"],
                "error_type": trial["error_type"],
                "error_message": trial["error_message"],
            }
            for trial in trials
            if not trial["recovery_success"]
        ],
    }


def write_outputs(
    output_dir: Path,
    trials: list[dict[str, Any]],
    summary: dict[str, Any],
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    trials_path = output_dir / "temporal_recovery_trials.csv"
    with trials_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(trials[0]))
        writer.writeheader()
        writer.writerows(trials)
    summary_path = output_dir / "temporal_recovery_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    latency = summary["recovery_latency_ms"]
    environment_data = summary["environment"]
    case_lines = [
        "| Case | Strategy | Fault boundary | Trials | Success | Success rate | P50 ms | P95 ms |",
        "|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for case in CASES:
        data = summary["cases"][case.case_id]
        case_lines.append(
            f"| {case.case_id} | {case.strategy} | {case.fault_boundary} | "
            f"{data['trials']} | {data['recovery_success']} | "
            f"{data['recovery_success_rate']:.2%} | "
            f"{data['recovery_latency_ms']['p50']} | {data['recovery_latency_ms']['p95']} |"
        )
    limitations = (
        "T3 kills the Workflow Worker after the fake tool's externally visible counter has "
        "advanced but before the Activity result is released. The Activity Worker remains alive. "
        "The separate PostgreSQL acceptance test "
        "`test/web_persistence/test_durable_agent_activity_worker_kill.py` covers Activity Worker "
        "loss in the non-idempotent ack gap and expects fail-closed `uncertain` handling. "
        "The benchmark therefore demonstrates replay-safe Workflow recovery, not universal "
        "exactly-once execution for arbitrary external tools."
    )
    report = f"""# Temporal Recovery Benchmark Report

## Environment

- Git commit: `{environment_data['git_commit']}`
- Branch: `{environment_data['branch']}`
- Timestamp: `{environment_data['timestamp']}`
- Python: `{environment_data['python_version']}`
- Temporal Python SDK: `{environment_data['temporal_sdk_version']}`
- Docker Compose: `{environment_data['docker_compose_version']}`
- Host: `{environment_data['host_os']}`

## Method

Each trial starts one Activity Worker and one Workflow Worker as real OS processes. After the
configured Activity exposes its boundary marker, the runner sends SIGKILL to the Workflow Worker,
releases the Activity, starts a replacement Workflow Worker, waits for the same Temporal Workflow,
and checks transcript version, unique operation ids, operation attempts, and side-effect count.

## Results

- Trials: **{summary['trials']}**
- Recovery success: **{summary['recovery_success']}**
- Recovery success rate: **{summary['recovery_success_rate']:.2%}**
- Duplicate-operation trials: **{summary['duplicate_operation_trials']}**
- Duplicate-operation rate: **{summary['duplicate_operation_rate']:.2%}**
- Duplicate-side-effect trials: **{summary['duplicate_side_effect_trials']}**
- Duplicate-side-effect rate: **{summary['duplicate_side_effect_rate']:.2%}**
- Recovery latency P50: **{latency['p50']} ms**
- Recovery latency P95: **{latency['p95']} ms**

## Results by Fault Boundary

{chr(10).join(case_lines)}

## Failure Analysis

Failures are retained verbatim in `temporal_recovery_summary.json` and each trial's state directory.
Observed failure count: **{len(summary['failures'])}**.

## Limitations

{limitations}

## Reproduction

```bash
docker compose up -d temporal-postgres temporal
docker compose exec -T temporal tctl --ns hpagent-benchmark namespace register --rd 1
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 TEMPORAL_NAMESPACE=hpagent-benchmark \\
  .venv/bin/python \\
  scripts/benchmarks/temporal_recovery_benchmark.py --trials-per-case 10
```
"""
    (output_dir / "temporal_recovery_report.md").write_text(report, encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    runs_dir = output_dir / "temporal_recovery_runs"
    if args.clean_runs and runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    config = {
        "host": args.host,
        "namespace": args.namespace,
        "trials_per_case": args.trials_per_case,
        "total_trials": args.trials_per_case * len(CASES),
        "trial_timeout_seconds": args.timeout,
        "cases": [asdict(case) for case in CASES],
    }
    env = environment(config)
    client = await Client.connect(args.host, namespace=args.namespace)
    trials: list[dict[str, Any]] = []
    for case in CASES:
        for index in range(1, args.trials_per_case + 1):
            trial = await run_trial(
                client,
                args.host,
                args.namespace,
                case,
                index,
                runs_dir,
                args.timeout,
            )
            trials.append(trial)
            status = "PASS" if trial["recovery_success"] else "FAIL"
            print(
                f"[{len(trials):02d}/{config['total_trials']}] {status} "
                f"{trial['trial_id']} {case.fault_boundary} "
                f"recovery={trial['recovery_latency_ms']}ms",
                flush=True,
            )
    summary = summarize(trials, env)
    write_outputs(output_dir, trials, summary)
    print(json.dumps({
        "trials": summary["trials"],
        "recovery_success": summary["recovery_success"],
        "recovery_success_rate": summary["recovery_success_rate"],
        "duplicate_operation_trials": summary["duplicate_operation_trials"],
        "duplicate_side_effect_trials": summary["duplicate_side_effect_trials"],
        "recovery_latency_ms": summary["recovery_latency_ms"],
    }, ensure_ascii=False, indent=2))
    return 0 if summary["recovery_success"] == summary["trials"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=os.getenv("TEMPORAL_HOST", "localhost:7233"))
    parser.add_argument("--namespace", default=os.getenv("TEMPORAL_NAMESPACE", "default"))
    parser.add_argument("--trials-per-case", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=45)
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts" / "benchmarks"))
    parser.add_argument("--clean-runs", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()
    if args.trials_per_case < 1:
        parser.error("--trials-per-case must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
