#!/usr/bin/env python3
"""Run real Activity Worker SIGKILL and ack-gap recovery trials."""
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
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ActivityError, ApplicationError

from agent_activities.store import AgentDataStore
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    CompactToolCall,
    ToolExecutionInput,
)
from persistence.migrate import migrate
from web_domain.services import CommandService

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "test" / "support" / "durable_worker_process.py"
CASES = (
    ("A1", 5, "none", "activity-before-side-effect"),
    ("A2", 5, "idempotent_write", "activity-idempotent-effect-succeeded-before-ack"),
    ("A3", 10, "non_idempotent_write", "activity-side-effect-succeeded-before-ack"),
)


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def replace_database(base_url: str, database: str) -> str:
    parsed = urlsplit(base_url)
    return urlunsplit((parsed.scheme, parsed.netloc, f"/{database}", "", ""))


def replace_credentials(base_url: str, username: str, password: str) -> str:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or "localhost"
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (parsed.scheme, f"{username}:{password}@{host}", parsed.path, "", "")
    )


def ensure_database(admin_url: str, name: str) -> tuple[str, str, str]:
    with psycopg.connect(admin_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    migration_url = replace_database(admin_url, name)
    migrate(migration_url)
    return (
        migration_url,
        replace_credentials(migration_url, "hpagent_api", "hpagent_api"),
        replace_credentials(migration_url, "hpagent_worker", "hpagent_worker"),
    )


def reset_database(migration_url: str) -> UUID:
    with psycopg.connect(migration_url, autocommit=True) as connection:
        tables = connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='hpagent' "
            "AND tablename<>'schema_migrations' ORDER BY tablename"
        ).fetchall()
        if tables:
            identifiers = [
                sql.SQL("{}.{}").format(sql.Identifier("hpagent"), sql.Identifier(row[0]))
                for row in tables
            ]
            connection.execute(
                sql.SQL("TRUNCATE {} CASCADE").format(sql.SQL(", ").join(identifiers))
            )
        account_id = uuid4()
        connection.execute(
            "INSERT INTO hpagent.accounts(account_id) VALUES (%s)", (account_id,)
        )
        return account_id


def create_account(migration_url: str) -> UUID:
    account_id = uuid4()
    with psycopg.connect(migration_url) as connection:
        connection.execute(
            "INSERT INTO hpagent.accounts(account_id) VALUES (%s)", (account_id,)
        )
    return account_id


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
    timeout: float,
    *,
    database_url: str | None = None,
    activity_case: str | None = None,
) -> asyncio.subprocess.Process:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(ROOT / "src"), environment.get("PYTHONPATH", "")) if item
    )
    command = [
        sys.executable,
        str(HARNESS),
        role,
        host,
        namespace,
        str(state_dir),
        ready_name,
    ]
    if database_url:
        command.extend(("--database-url", database_url))
    if activity_case:
        command.extend(("--activity-case", activity_case))
    process = await asyncio.create_subprocess_exec(*command, env=environment)
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


def tool_request(case_id: str, run_id: str, operation_id: str) -> ToolExecutionInput:
    tool_name = {
        "A1": "benchmark_no_side_effect",
        "A2": "benchmark_idempotent_write",
        "A3": "counting_write",
    }[case_id]
    return ToolExecutionInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        "react",
        f"transcript:{run_id}",
        1,
        1,
        operation_id,
        1,
        CompactToolCall("call-1", tool_name, f"decision:{operation_id}#call-1"),
    )


def prepare_a3(
    app_url: str,
    worker_url: str,
    account_id: UUID,
) -> tuple[ToolExecutionInput, str]:
    commands = CommandService(app_url)
    conversation = commands.create_conversation(
        account_id, str(uuid4()), "activity ack-gap benchmark"
    )
    sent = commands.send_message(
        account_id,
        UUID(conversation["conversation_id"]),
        str(uuid4()),
        "execute benchmark non-idempotent write",
    )
    run = sent["run"]
    run_id = str(run["run_id"])
    session_id = str(run["session_id"])
    transcript_id = f"transcript:{run_id}"
    store = AgentDataStore(worker_url, lease_ttl_seconds=900)
    lease = store.acquire_lease(str(account_id), run_id)
    context_operation = f"{run_id}:react:context"
    if store.begin_operation(context_operation, run_id, "context") is not None:
        raise RuntimeError("unexpected duplicate context operation")
    store.create_transcript(
        transcript_id=transcript_id,
        run_id=run_id,
        account_id=str(account_id),
        conversation_id=conversation["conversation_id"],
        session_id=session_id,
        messages=[{"role": "user", "content": "execute benchmark write"}],
        operation_id=context_operation,
    )
    decision_operation = f"{run_id}:react:turn:1:model"
    decision_ref = f"agent-decision:{decision_operation}"
    if store.begin_operation(decision_operation, run_id, "model") is not None:
        raise RuntimeError("unexpected duplicate model operation")
    store.complete_operation(
        decision_operation,
        decision_ref,
        {
            "schema_version": AGENT_SCHEMA_VERSION,
            "operation_id": decision_operation,
            "decision_type": "tool_calls",
            "decision_ref": decision_ref,
            "tool_call_arguments": {"call-1": {"value": 1}},
            "transcript_version": 1,
        },
    )
    operation_id = f"{run_id}:react:turn:1:tool:call-1"
    request = ToolExecutionInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        str(account_id),
        conversation["conversation_id"],
        session_id,
        "react",
        transcript_id,
        1,
        1,
        operation_id,
        lease.fencing_token,
        CompactToolCall("call-1", "counting_write", f"{decision_ref}#call-1"),
    )
    return request, operation_id


def fake_counts(state_dir: Path, operation_id: str) -> tuple[int, int, int]:
    database = state_dir / "activity_recovery.sqlite"
    if not database.exists():
        return 0, 0, 0
    with sqlite3.connect(database) as connection:
        attempts = connection.execute(
            "SELECT count(*) FROM activity_attempts WHERE operation_id=?", (operation_id,)
        ).fetchone()[0]
        state_count = 0
        invocations = 0
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "idempotent_business_state" in tables:
            state_count = connection.execute(
                "SELECT count(*) FROM idempotent_business_state WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
        if "effect_invocations" in tables:
            invocations = connection.execute(
                "SELECT count(*) FROM effect_invocations WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
    return int(attempts), int(state_count), int(invocations)


def a3_facts(
    migration_url: str, state_dir: Path, operation_id: str
) -> tuple[str | None, str | None, int, str | None, int]:
    with psycopg.connect(migration_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        row = connection.execute(
            "SELECT status,error_code,attempt_count,result_payload->>'side_effect_class' "
            "FROM agent_operations WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
    side_effect_count = 0
    external = state_dir / "external.sqlite"
    if external.exists():
        with sqlite3.connect(external) as connection:
            effect = connection.execute(
                "SELECT count FROM side_effect_counter WHERE operation_id=?", (operation_id,)
            ).fetchone()
            side_effect_count = int(effect[0]) if effect else 0
    if row is None:
        return None, None, 0, None, side_effect_count
    return str(row[0]), row[1], int(row[2]), row[3], side_effect_count


async def run_trial(
    client: Client,
    case_id: str,
    index: int,
    host: str,
    namespace: str,
    runs_dir: Path,
    timeout: float,
    migration_url: str,
    app_url: str,
    worker_url: str,
    account_id: UUID,
) -> dict[str, Any]:
    trial_id = f"{case_id.lower()}-{index:02d}-{uuid4().hex[:8]}"
    state_dir = runs_dir / trial_id
    state_dir.mkdir(parents=True, exist_ok=False)
    if case_id == "A3":
        trial_account_id = await asyncio.to_thread(create_account, migration_url)
        request, operation_id = await asyncio.to_thread(
            prepare_a3, app_url, worker_url, trial_account_id
        )
    else:
        run_id = str(uuid4())
        operation_id = f"{run_id}:activity:{case_id.lower()}"
        request = tool_request(case_id, run_id, operation_id)
    boundary = dict((case, marker) for case, _, _, marker in CASES)[case_id]
    side_effect_class = dict((case, effect) for case, _, effect, _ in CASES)[case_id]
    workflow_id = f"activity-worker-recovery-{case_id.lower()}-{request.run_id}"
    record: dict[str, Any] = {
        "experiment": "activity_worker_recovery",
        "trial_id": trial_id,
        "case_id": case_id,
        "strategy": "react",
        "fault_boundary": boundary,
        "workflow_id": workflow_id,
        "run_id": request.run_id,
        "operation_id": operation_id,
        "tool": request.tool_call.name,
        "side_effect_class": side_effect_class,
        "activity_worker_killed": False,
        "replacement_activity_worker_started": False,
        "activity_attempts": 0,
        "side_effect_count_expected_max": 1 if case_id in {"A2", "A3"} else 0,
        "side_effect_count_actual": 0,
        "effect_invocation_count": 0,
        "duplicate_side_effect": False,
        "operation_final_state": None,
        "workflow_final_status": None,
        "safe_outcome": False,
        "outcome_type": None,
        "kill_to_terminal_latency_ms": None,
        "error_type": None,
        "error_message": None,
        "state_dir": display_path(state_dir),
    }
    workflow_worker = activity_worker = replacement_activity_worker = None
    killed_at: float | None = None
    try:
        workflow_worker = await start_worker(
            "workflow", host, namespace, state_dir, "workflow", timeout
        )
        activity_role = "production-activity" if case_id == "A3" else "benchmark-activity"
        activity_worker = await start_worker(
            activity_role,
            host,
            namespace,
            state_dir,
            "activity-1",
            timeout,
            database_url=worker_url if case_id == "A3" else None,
            activity_case=case_id if case_id != "A3" else None,
        )
        handle = await client.start_workflow(
            "activity-crash-tool-workflow",
            request,
            id=workflow_id,
            task_queue=AGENT_TASK_QUEUE,
        )
        await wait_for_path(state_dir / f"{boundary}.boundary", timeout)
        if case_id == "A3":
            state, _, attempts, _, effects = await asyncio.to_thread(
                a3_facts, migration_url, state_dir, operation_id
            )
            if state != "intent_recorded" or attempts != 1 or effects != 1:
                raise RuntimeError(
                    "A3 kill boundary is not inside the confirmed side-effect ack gap"
                )
        killed_at = time.monotonic()
        await terminate(activity_worker)
        record["activity_worker_killed"] = True
        replacement_activity_worker = await start_worker(
            activity_role,
            host,
            namespace,
            state_dir,
            "activity-2",
            timeout,
            database_url=worker_url if case_id == "A3" else None,
            activity_case=case_id if case_id != "A3" else None,
        )
        record["replacement_activity_worker_started"] = True
        if case_id in {"A1", "A2"}:
            result = await asyncio.wait_for(handle.result(), timeout=timeout)
            record["workflow_final_status"] = "completed"
            attempts, state_count, invocations = fake_counts(state_dir, operation_id)
            record["activity_attempts"] = attempts
            record["side_effect_count_actual"] = state_count
            record["effect_invocation_count"] = invocations
            result_correct = result.get("display_summary") == "expected-result"
            expected_effects = 0 if case_id == "A1" else 1
            record["duplicate_side_effect"] = state_count > expected_effects
            record["operation_final_state"] = "completed"
            record["safe_outcome"] = (
                attempts >= 2
                and state_count == expected_effects
                and result_correct
                and not record["duplicate_side_effect"]
            )
            record["outcome_type"] = (
                "eventually_completed" if record["safe_outcome"] else "unexpected_failure"
            )
        else:
            failure_type: str | None = None
            try:
                await asyncio.wait_for(handle.result(), timeout=timeout)
                record["workflow_final_status"] = "completed"
            except WorkflowFailureError as failure:
                record["workflow_final_status"] = "failed"
                if isinstance(failure.cause, ActivityError) and isinstance(
                    failure.cause.cause, ApplicationError
                ):
                    failure_type = failure.cause.cause.type
            state, error_code, attempts, persisted_class, effects = await asyncio.to_thread(
                a3_facts, migration_url, state_dir, operation_id
            )
            record["operation_final_state"] = state
            record["activity_attempts"] = attempts
            record["side_effect_count_actual"] = effects
            record["effect_invocation_count"] = effects
            record["duplicate_side_effect"] = effects > 1
            record["safe_outcome"] = all(
                (
                    record["workflow_final_status"] == "failed",
                    failure_type == "tool_side_effect_uncertain",
                    state == "uncertain",
                    error_code == "tool_side_effect_uncertain",
                    persisted_class == "non_idempotent_write",
                    attempts >= 2,
                    effects == 1,
                )
            )
            record["outcome_type"] = (
                "uncertain_fail_closed"
                if record["safe_outcome"]
                else (
                    "unsafe_duplicate_side_effect"
                    if record["duplicate_side_effect"]
                    else "unexpected_failure"
                )
            )
        record["kill_to_terminal_latency_ms"] = round(
            (time.monotonic() - killed_at) * 1000, 3
        )
        if not record["safe_outcome"]:
            record["error_type"] = "RecoveryInvariantViolation"
            record["error_message"] = "Activity recovery invariants were not satisfied"
    except Exception as exc:
        if killed_at is not None:
            record["kill_to_terminal_latency_ms"] = round(
                (time.monotonic() - killed_at) * 1000, 3
            )
        record["error_type"] = type(exc).__name__
        record["error_message"] = str(exc)[:2000]
        record["outcome_type"] = record["outcome_type"] or "unexpected_failure"
    finally:
        await terminate(replacement_activity_worker)
        await terminate(activity_worker)
        await terminate(workflow_worker)
    return record


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
        result = subprocess.run(
            command,
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def environment(config: dict[str, Any]) -> dict[str, Any]:
    try:
        temporal_sdk = version("temporalio")
    except PackageNotFoundError:
        temporal_sdk = "unavailable"
    return {
        "git_commit": command_output(["git", "rev-parse", "HEAD"]),
        "branch": command_output(["git", "branch", "--show-current"]),
        "timestamp": utc_now(),
        "python_version": platform.python_version(),
        "temporal_sdk_version": temporal_sdk,
        "docker_compose_version": command_output(["docker", "compose", "version", "--short"]),
        "host_os": platform.platform(),
        "logical_cpu_count": os.cpu_count(),
        "benchmark_config": config,
    }


def summarize(trials: list[dict[str, Any]], env: dict[str, Any]) -> dict[str, Any]:
    latencies = [
        float(trial["kill_to_terminal_latency_ms"])
        for trial in trials
        if trial["safe_outcome"] and trial["kill_to_terminal_latency_ms"] is not None
    ]

    def case_summary(case_id: str) -> dict[str, Any]:
        selected = [trial for trial in trials if trial["case_id"] == case_id]
        return {
            "trials": len(selected),
            "safe_outcomes": sum(trial["safe_outcome"] for trial in selected),
            "safe_outcome_rate": (
                sum(trial["safe_outcome"] for trial in selected) / len(selected)
                if selected
                else None
            ),
            "activity_retry_observed": sum(
                int(trial["activity_attempts"]) >= 2 for trial in selected
            ),
            "eventually_completed": sum(
                trial["workflow_final_status"] == "completed" for trial in selected
            ),
        }

    a3 = [trial for trial in trials if trial["case_id"] == "A3"]
    return {
        "experiment": "activity_worker_recovery",
        "environment": env,
        "definitions": {
            "safe_outcome_rate": "trials with correct retry completion or non-idempotent uncertain fail-closed outcome / all trials",
            "activity_retry_observed_rate": "trials with persisted activity attempt count >= 2 / all trials",
            "unsafe_duplicate_side_effect_rate": "A3 trials with independently observed external side-effect count > 1 / all A3 trials",
            "kill_to_terminal_latency_ms": "monotonic elapsed time from Activity Worker SIGKILL until Workflow terminal result",
            "percentile_method": "linear interpolation at (n-1)*q over safe outcomes",
        },
        "trials": len(trials),
        "safe_outcomes": sum(trial["safe_outcome"] for trial in trials),
        "safe_outcome_rate": sum(trial["safe_outcome"] for trial in trials) / len(trials),
        "unexpected_failures": sum(
            trial["outcome_type"] == "unexpected_failure" for trial in trials
        ),
        "unexpected_failure_rate": sum(
            trial["outcome_type"] == "unexpected_failure" for trial in trials
        )
        / len(trials),
        "activity_retry_observed": sum(
            int(trial["activity_attempts"]) >= 2 for trial in trials
        ),
        "activity_retry_observed_rate": sum(
            int(trial["activity_attempts"]) >= 2 for trial in trials
        )
        / len(trials),
        "kill_to_terminal_latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "cases": {case_id: case_summary(case_id) for case_id, _, _, _ in CASES},
        "a3": {
            "safe_reconciled_success_count": sum(
                trial["outcome_type"] == "reconciled_success" for trial in a3
            ),
            "uncertain_fail_closed_count": sum(
                trial["outcome_type"] == "uncertain_fail_closed" for trial in a3
            ),
            "unsafe_duplicate_side_effect_count": sum(
                trial["duplicate_side_effect"] for trial in a3
            ),
            "unsafe_duplicate_side_effect_rate": (
                sum(trial["duplicate_side_effect"] for trial in a3) / len(a3)
            ),
        },
        "failures": [
            {
                "trial_id": trial["trial_id"],
                "case_id": trial["case_id"],
                "error_type": trial["error_type"],
                "error_message": trial["error_message"],
            }
            for trial in trials
            if not trial["safe_outcome"]
        ],
    }


def write_outputs(
    output_dir: Path, trials: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    with (output_dir / "activity_worker_recovery_trials.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(trials[0]))
        writer.writeheader()
        writer.writerows(trials)
    (output_dir / "activity_worker_recovery_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    latency = summary["kill_to_terminal_latency_ms"]
    env = summary["environment"]
    report = f"""# Activity Worker Recovery and Ack-Gap Report

## Environment

- Git commit: `{env['git_commit']}`
- Branch: `{env['branch']}`
- Timestamp: `{env['timestamp']}`
- Python: `{env['python_version']}`
- Temporal Python SDK: `{env['temporal_sdk_version']}`
- Temporal namespace: `{env['benchmark_config']['temporal_namespace']}`
- PostgreSQL database: `{env['benchmark_config']['database_name']}`

## Method

Every trial kept a real Workflow Worker alive, killed a separate Activity Worker process with
SIGKILL at an explicit boundary marker, started a replacement Activity Worker, and observed the
same Temporal Activity retry. A1 killed before any effect. A2 killed after an idempotent state
write but before Activity completion. A3 used the production DurableAgentActivities,
AgentDataStore operation intent, fencing token, non-idempotent classification, and unsupported
reconciler. Its external effect counter lived in an independent SQLite file.

## Results

- Trials: **{summary['trials']}**
- Safe outcomes: **{summary['safe_outcomes']}**
- Safe outcome rate: **{summary['safe_outcome_rate']:.2%}**
- Activity retry observed: **{summary['activity_retry_observed']}**
- Activity retry observed rate: **{summary['activity_retry_observed_rate']:.2%}**
- Unexpected failures: **{summary['unexpected_failures']}**
- Kill-to-terminal latency P50: **{latency['p50']} ms**
- Kill-to-terminal latency P95: **{latency['p95']} ms**

| Case | Trials | Safe | Eventually completed | Retry observed |
|---|---:|---:|---:|---:|
| A1 pre-side-effect | {summary['cases']['A1']['trials']} | {summary['cases']['A1']['safe_outcomes']} | {summary['cases']['A1']['eventually_completed']} | {summary['cases']['A1']['activity_retry_observed']} |
| A2 idempotent ack gap | {summary['cases']['A2']['trials']} | {summary['cases']['A2']['safe_outcomes']} | {summary['cases']['A2']['eventually_completed']} | {summary['cases']['A2']['activity_retry_observed']} |
| A3 non-idempotent ack gap | {summary['cases']['A3']['trials']} | {summary['cases']['A3']['safe_outcomes']} | {summary['cases']['A3']['eventually_completed']} | {summary['cases']['A3']['activity_retry_observed']} |

### A3 Safety Outcomes

- Safe reconciled success: **{summary['a3']['safe_reconciled_success_count']}**
- Uncertain fail-closed: **{summary['a3']['uncertain_fail_closed_count']}**
- Unsafe duplicate side effects: **{summary['a3']['unsafe_duplicate_side_effect_count']}**
- Unsafe duplicate side-effect rate: **{summary['a3']['unsafe_duplicate_side_effect_rate']:.2%}**

## Interpretation and Boundary

A3 safe failures are not Workflow recovery successes: the external write occurred once, its
Activity completion was lost, and unsupported reconciliation forced the stable operation into
`uncertain` with a non-retryable `tool_side_effect_uncertain` failure. This is fail-closed safety,
not exactly-once delivery and not automatic business reconciliation.

## Failures

Unexpected failure count: **{len(summary['failures'])}**. Raw details are retained in the CSV and
JSON summary.
"""
    (output_dir / "activity_worker_recovery_report.md").write_text(
        report, encoding="utf-8"
    )


def merge_temporal_report(output_dir: Path, summary: dict[str, Any]) -> None:
    path = output_dir / "temporal_recovery_report.md"
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    marker = "\n## B. Activity Worker Loss\n"
    if marker in text:
        text = text.split(marker, 1)[0].rstrip() + "\n"
    if "## A. Workflow Worker Loss" not in text:
        text = text.replace("## Method\n", "## A. Workflow Worker Loss\n\n### Method\n", 1)
        for heading in (
            "Results",
            "Results by Fault Boundary",
            "Failure Analysis",
            "Limitations",
            "Reproduction",
        ):
            text = text.replace(f"## {heading}\n", f"### {heading}\n", 1)
    latency = summary["kill_to_terminal_latency_ms"]
    text += f"""

## B. Activity Worker Loss

Real Activity Worker SIGKILL supplement: **{summary['safe_outcomes']}/{summary['trials']}** safe
outcomes with Activity retry observed in **{summary['activity_retry_observed']}/{summary['trials']}**
trials. A3 produced **{summary['a3']['uncertain_fail_closed_count']}** safe uncertain/fail-closed
outcomes and **{summary['a3']['unsafe_duplicate_side_effect_count']}** unsafe duplicate side
effects. Kill-to-terminal latency was P50 **{latency['p50']} ms**, P95 **{latency['p95']} ms**.

See `activity_worker_recovery_report.md` and `activity_worker_recovery_trials.csv` for method,
per-trial evidence, and the exact safety boundary. This result demonstrates Activity retry and
ack-gap fail-closed safety; it does not claim universal Tool exactly-once execution.
"""
    path.write_text(text, encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runs_dir = output_dir / "activity_worker_recovery_runs"
    if runs_dir.exists():
        shutil.rmtree(runs_dir)
    runs_dir.mkdir(parents=True)
    migration_url, app_url, worker_url = ensure_database(
        args.admin_database_url, args.database_name
    )
    account_id = reset_database(migration_url)
    cases = (
        ("A1", args.trials_a1, "none", "activity-before-side-effect"),
        (
            "A2",
            args.trials_a2,
            "idempotent_write",
            "activity-idempotent-effect-succeeded-before-ack",
        ),
        (
            "A3",
            args.trials_a3,
            "non_idempotent_write",
            "activity-side-effect-succeeded-before-ack",
        ),
    )
    config = {
        "temporal_host": args.temporal_host,
        "temporal_namespace": args.temporal_namespace,
        "database_name": args.database_name,
        "cases": {case: count for case, count, _, _ in cases},
        "timeout_seconds": args.timeout,
    }
    env = environment(config)
    client = await Client.connect(args.temporal_host, namespace=args.temporal_namespace)
    trials: list[dict[str, Any]] = []
    total = sum(count for _, count, _, _ in cases)
    for case_id, count, _, _ in cases:
        for index in range(1, count + 1):
            trial = await run_trial(
                client,
                case_id,
                index,
                args.temporal_host,
                args.temporal_namespace,
                runs_dir,
                args.timeout,
                migration_url,
                app_url,
                worker_url,
                account_id,
            )
            trials.append(trial)
            print(
                f"[{len(trials):02d}/{total}] "
                f"{'SAFE' if trial['safe_outcome'] else 'FAIL'} {trial['trial_id']} "
                f"attempts={trial['activity_attempts']} effects={trial['side_effect_count_actual']} "
                f"outcome={trial['outcome_type']}",
                flush=True,
            )
    summary = summarize(trials, env)
    write_outputs(output_dir, trials, summary)
    merge_temporal_report(output_dir, summary)
    print(
        json.dumps(
            {
                "trials": summary["trials"],
                "safe_outcomes": summary["safe_outcomes"],
                "safe_outcome_rate": summary["safe_outcome_rate"],
                "activity_retry_observed_rate": summary[
                    "activity_retry_observed_rate"
                ],
                "a3": summary["a3"],
                "kill_to_terminal_latency_ms": summary[
                    "kill_to_terminal_latency_ms"
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not summary["failures"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temporal-host", default=os.getenv("TEMPORAL_HOST", "localhost:7233"))
    parser.add_argument(
        "--temporal-namespace",
        default=os.getenv("TEMPORAL_NAMESPACE", "hpagent-activity-benchmark"),
    )
    parser.add_argument(
        "--admin-database-url",
        default=os.getenv(
            "ACTIVITY_BENCHMARK_ADMIN_DATABASE_URL",
            "postgresql://hpagent_migrate:hpagent_migrate@localhost:5434/postgres",
        ),
    )
    parser.add_argument("--database-name", default="hpagent_activity_benchmark")
    parser.add_argument("--trials-a1", type=int, default=5)
    parser.add_argument("--trials-a2", type=int, default=5)
    parser.add_argument("--trials-a3", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts" / "benchmarks"))
    args = parser.parse_args()
    if not args.database_name.startswith("hpagent_") or not args.database_name.endswith(
        "_benchmark"
    ):
        parser.error("--database-name must be explicitly scoped as hpagent_*_benchmark")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if min(args.trials_a1, args.trials_a2, args.trials_a3) < 1:
        parser.error("all Activity case trial counts must be at least 1")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
