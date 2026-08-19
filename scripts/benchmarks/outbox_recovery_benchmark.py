#!/usr/bin/env python3
"""Benchmark Transactional Outbox recovery after the Web Worker is unavailable."""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import platform
import shutil
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
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row
from temporalio.client import Client

from persistence.migrate import migrate
from web_api.app import create_app
from web_api.config import WebApiSettings

ROOT = Path(__file__).resolve().parents[2]
WORKER_HARNESS = ROOT / "test" / "support" / "outbox_benchmark_worker.py"


class BenchmarkCredentials:
    def verify(self, username: str, password: str) -> str | None:
        if username.strip().casefold() == "outbox-benchmark" and password == "benchmark-password":
            return "outbox-benchmark"
        return None


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def database_url(base_url: str, database: str, username: str | None = None) -> str:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or "localhost"
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    user = username or parsed.username
    password = parsed.password
    credentials = ""
    if user:
        credentials = user
        if password:
            credentials += f":{password}"
        credentials += "@"
    return urlunsplit((parsed.scheme, credentials + host, f"/{database}", "", ""))


def replace_credentials(base_url: str, username: str, password: str) -> str:
    parsed = urlsplit(base_url)
    hostname = parsed.hostname or "localhost"
    host = f"[{hostname}]" if ":" in hostname else hostname
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunsplit(
        (parsed.scheme, f"{username}:{password}@{host}", parsed.path, "", "")
    )


def ensure_benchmark_database(admin_url: str, name: str) -> tuple[str, str, str]:
    admin_database = urlsplit(admin_url).path.lstrip("/") or "postgres"
    control_url = database_url(admin_url, admin_database)
    with psycopg.connect(control_url, autocommit=True) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (name,)
        ).fetchone()
        if not exists:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    migration_url = database_url(admin_url, name)
    migrate(migration_url)
    app_url = replace_credentials(migration_url, "hpagent_api", "hpagent_api")
    worker_url = replace_credentials(migration_url, "hpagent_worker", "hpagent_worker")
    return migration_url, app_url, worker_url


def reset_benchmark_data(migration_url: str) -> None:
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


def seed_identity(migration_url: str) -> UUID:
    account_id, binding_id = uuid4(), uuid4()
    with psycopg.connect(migration_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        connection.execute("INSERT INTO accounts(account_id) VALUES (%s)", (account_id,))
        connection.execute(
            "INSERT INTO identity_bindings(identity_binding_id,account_id,provider,"
            "external_subject_id,normalized_subject_id,verified_at) "
            "VALUES (%s,%s,'web',%s,%s,now())",
            (binding_id, account_id, "outbox-benchmark", "outbox-benchmark"),
        )
    return account_id


def api_settings(app_url: str, worker_url: str) -> WebApiSettings:
    return WebApiSettings(
        database_url=app_url,
        worker_database_url=worker_url,
        public_origin="https://outbox-benchmark",
        cursor_signing_keys={"benchmark": b"benchmark-cursor-key-32-bytes-minimum"},
        active_cursor_key_id="benchmark",
        session_token_pepper=b"benchmark-session-key-32-bytes-minimum",
        csrf_signing_key=b"benchmark-csrf-key-32-bytes-minimum!!!",
        environment="test",
        cookie_secure=True,
        fake_executor_enabled=False,
        redis_url=None,
    )


def command_headers(csrf: str) -> dict[str, str]:
    return {
        "Origin": "https://outbox-benchmark",
        "X-CSRF-Token": csrf,
        "Idempotency-Key": str(uuid4()),
    }


def accept_requests(app_url: str, worker_url: str, count: int) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with TestClient(
        create_app(api_settings(app_url, worker_url), BenchmarkCredentials()),
        base_url="https://outbox-benchmark",
    ) as client:
        login = client.post(
            "/auth/login",
            json={
                "username": "outbox-benchmark",
                "password": "benchmark-password",
                "return_to": "/",
            },
            follow_redirects=False,
        )
        if login.status_code != 303:
            raise RuntimeError(f"benchmark login failed: HTTP {login.status_code}")
        me = client.get("/api/v1/me")
        if me.status_code != 200:
            raise RuntimeError(f"benchmark identity lookup failed: HTTP {me.status_code}")
        csrf = str(me.json()["csrf_token"])
        for index in range(1, count + 1):
            conversation_response = client.post(
                "/api/v1/conversations",
                json={"title": f"Outbox recovery {index:02d}"},
                headers=command_headers(csrf),
            )
            if conversation_response.status_code != 201:
                raise RuntimeError(
                    f"conversation {index} failed: HTTP {conversation_response.status_code}"
                )
            conversation_id = conversation_response.json()["conversation"]["conversation_id"]
            accepted_at = utc_now()
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": f"outbox benchmark request {index:02d}"},
                headers=command_headers(csrf),
            )
            body = response.json()
            record: dict[str, Any] = {
                "experiment": "outbox_recovery",
                "trial_id": f"outbox-{index:02d}",
                "request_index": index,
                "http_status": response.status_code,
                "conversation_id": conversation_id,
                "message_id": body.get("user_message", {}).get("message_id"),
                "run_id": body.get("run", {}).get("run_id"),
                "outbox_event_id": None,
                "db_message_persisted": False,
                "db_run_persisted": False,
                "outbox_pending_while_worker_down": False,
                "worker_restarted": False,
                "outbox_processed": False,
                "workflow_started": False,
                "workflow_completed": False,
                "run_terminal": False,
                "duplicate_workflow_count": 0,
                "lost": True,
                "accepted_at": accepted_at,
                "processed_at": None,
                "eventual_completion_latency_ms": None,
                "error_type": None,
                "error_message": None,
            }
            if response.status_code != 202:
                record["error_type"] = "HttpAcceptanceFailure"
                record["error_message"] = json.dumps(body, ensure_ascii=False)[:2000]
            records.append(record)
    return records


def load_initial_facts(migration_url: str, records: list[dict[str, Any]]) -> None:
    with psycopg.connect(migration_url, row_factory=dict_row) as connection:
        connection.execute("SET search_path=hpagent,public")
        for record in records:
            if not record["run_id"]:
                continue
            row = connection.execute(
                "SELECT r.run_id,r.status AS run_status,m.message_id,"
                "o.outbox_event_id,o.status AS outbox_status,"
                "(SELECT count(*) FROM workflow_executions w WHERE w.run_id=r.run_id) "
                "AS workflow_count "
                "FROM runs r "
                "LEFT JOIN messages m ON m.message_id=%s "
                "LEFT JOIN outbox_events o ON o.run_id=r.run_id AND o.event_type='start_run' "
                "WHERE r.run_id=%s",
                (record["message_id"], record["run_id"]),
            ).fetchone()
            if row is None:
                continue
            record["outbox_event_id"] = str(row["outbox_event_id"])
            record["db_message_persisted"] = row["message_id"] is not None
            record["db_run_persisted"] = row["run_id"] is not None
            record["outbox_pending_while_worker_down"] = (
                row["outbox_status"] == "pending"
                and row["run_status"] == "queued"
                and int(row["workflow_count"]) == 0
            )


async def wait_for_path(path: Path, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.05)


async def start_worker(
    temporal_host: str,
    temporal_namespace: str,
    worker_url: str,
    ready_file: Path,
    timeout: float,
) -> asyncio.subprocess.Process:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        item
        for item in (str(ROOT / "src"), environment.get("PYTHONPATH", ""))
        if item
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(WORKER_HARNESS),
        "--temporal-host",
        temporal_host,
        "--temporal-namespace",
        temporal_namespace,
        "--worker-database-url",
        worker_url,
        "--ready-file",
        str(ready_file),
        env=environment,
    )
    try:
        await wait_for_path(ready_file, timeout)
    except BaseException:
        await terminate(process)
        raise
    if process.returncode is not None:
        raise RuntimeError(f"benchmark Worker exited during startup: {process.returncode}")
    return process


async def terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)


def terminal_count(migration_url: str) -> tuple[int, int]:
    with psycopg.connect(migration_url) as connection:
        connection.execute("SET search_path=hpagent,public")
        completed = connection.execute(
            "SELECT count(*) FROM runs WHERE status='completed'"
        ).fetchone()[0]
        processed = connection.execute(
            "SELECT count(*) FROM outbox_events "
            "WHERE event_type='start_run' AND status='processed'"
        ).fetchone()[0]
        return int(completed), int(processed)


async def wait_for_completion(migration_url: str, expected: int, timeout: float) -> None:
    async with asyncio.timeout(timeout):
        while terminal_count(migration_url) != (expected, expected):
            await asyncio.sleep(0.1)


async def wait_for_temporal_results(
    temporal_host: str,
    temporal_namespace: str,
    records: list[dict[str, Any]],
    timeout: float,
) -> None:
    client = await Client.connect(temporal_host, namespace=temporal_namespace)

    async def wait_one(record: dict[str, Any]) -> None:
        result = await client.get_workflow_handle(
            f"hpagent-web-run-{record['run_id']}"
        ).result()
        record["workflow_completed"] = (
            result.get("outcome") == "completed" and result.get("run_id") == record["run_id"]
        )

    await asyncio.wait_for(
        asyncio.gather(*(wait_one(record) for record in records if record["run_id"])),
        timeout=timeout,
    )


def load_final_facts(migration_url: str, records: list[dict[str, Any]]) -> None:
    with psycopg.connect(migration_url, row_factory=dict_row) as connection:
        connection.execute("SET search_path=hpagent,public")
        for record in records:
            if not record["run_id"]:
                continue
            row = connection.execute(
                "SELECT r.status AS run_status,r.finished_at,o.status AS outbox_status,"
                "o.processed_at,w.workflow_id,w.temporal_run_id,"
                "(SELECT count(*) FROM workflow_executions wx WHERE wx.run_id=r.run_id) "
                "AS workflow_count,"
                "(SELECT status FROM messages ma WHERE ma.produced_by_run_id=r.run_id) "
                "AS assistant_status "
                "FROM runs r "
                "LEFT JOIN outbox_events o ON o.run_id=r.run_id AND o.event_type='start_run' "
                "LEFT JOIN workflow_executions w ON w.run_id=r.run_id AND w.is_current "
                "WHERE r.run_id=%s",
                (record["run_id"],),
            ).fetchone()
            if row is None:
                continue
            workflow_count = int(row["workflow_count"])
            record["worker_restarted"] = True
            record["outbox_processed"] = row["outbox_status"] == "processed"
            record["workflow_started"] = (
                row["workflow_id"] == f"hpagent-web-run-{record['run_id']}"
                and row["temporal_run_id"] is not None
            )
            record["run_terminal"] = (
                row["run_status"] == "completed" and row["assistant_status"] == "completed"
            )
            record["duplicate_workflow_count"] = max(workflow_count - 1, 0)
            record["lost"] = not all(
                (
                    record["db_message_persisted"],
                    record["db_run_persisted"],
                    record["outbox_processed"],
                    record["workflow_started"],
                    record["workflow_completed"],
                    record["run_terminal"],
                    record["duplicate_workflow_count"] == 0,
                )
            )
            record["processed_at"] = (
                row["processed_at"].isoformat() if row["processed_at"] else None
            )
            if row["finished_at"]:
                accepted = datetime.fromisoformat(record["accepted_at"])
                record["eventual_completion_latency_ms"] = round(
                    (row["finished_at"] - accepted).total_seconds() * 1000, 3
                )
            if record["lost"] and not record["error_type"]:
                record["error_type"] = "RecoveryInvariantViolation"
                record["error_message"] = json.dumps(
                    {
                        "run_status": row["run_status"],
                        "outbox_status": row["outbox_status"],
                        "workflow_count": workflow_count,
                        "assistant_status": row["assistant_status"],
                    },
                    ensure_ascii=False,
                    default=str,
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
        result = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            check=True,
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


def summarize(
    records: list[dict[str, Any]], env: dict[str, Any], backlog_drain_ms: float
) -> dict[str, Any]:
    accepted = [record for record in records if record["http_status"] == 202]
    latencies = [
        float(record["eventual_completion_latency_ms"])
        for record in records
        if record["eventual_completion_latency_ms"] is not None and not record["lost"]
    ]
    processed = [record for record in accepted if record["outbox_processed"]]
    return {
        "experiment": "outbox_recovery",
        "environment": env,
        "definitions": {
            "outbox_recovery_success_rate": "accepted runs whose start_run Outbox was processed, Temporal Workflow completed, and Run reached a terminal completed state / accepted requests",
            "lost_run_count": "accepted requests missing any persisted message/run, processed start event, deterministic workflow start, or terminal run",
            "duplicate_workflow_count": "workflow_execution rows beyond one per run",
            "backlog_drain_time_ms": "monotonic elapsed time from replacement Worker readiness until all start events were processed and all runs completed",
            "percentile_method": "linear interpolation at (n-1)*q over successful requests",
        },
        "requests": len(records),
        "accepted_requests": len(accepted),
        "persisted_requests": sum(
            record["db_message_persisted"] and record["db_run_persisted"]
            for record in accepted
        ),
        "eventually_processed_runs": len(processed),
        "temporal_completed_workflows": sum(
            record["workflow_completed"] for record in accepted
        ),
        "terminal_completed_runs": sum(record["run_terminal"] for record in accepted),
        "lost_run_count": sum(record["lost"] for record in accepted),
        "duplicate_workflow_count": sum(
            int(record["duplicate_workflow_count"]) for record in accepted
        ),
        "outbox_recovery_success_rate": (
            sum(not record["lost"] for record in accepted) / len(accepted)
            if accepted
            else None
        ),
        "backlog_drain_time_ms": round(backlog_drain_ms, 3),
        "eventual_completion_latency_ms": {
            "p50": percentile(latencies, 0.5),
            "p95": percentile(latencies, 0.95),
        },
        "failures": [
            {
                "trial_id": record["trial_id"],
                "error_type": record["error_type"],
                "error_message": record["error_message"],
            }
            for record in records
            if record["http_status"] != 202 or record["lost"]
        ],
    }


def write_outputs(
    output_dir: Path, records: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / "outbox_recovery_trials.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    (output_dir / "outbox_recovery_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    latency = summary["eventual_completion_latency_ms"]
    env = summary["environment"]
    report = f"""# Transactional Outbox Recovery Benchmark Report

## Environment

- Git commit: `{env['git_commit']}`
- Branch: `{env['branch']}`
- Timestamp: `{env['timestamp']}`
- Python: `{env['python_version']}`
- Temporal Python SDK: `{env['temporal_sdk_version']}`
- Docker Compose: `{env['docker_compose_version']}`
- Host: `{env['host_os']}`
- Temporal namespace: `{env['benchmark_config']['temporal_namespace']}`
- PostgreSQL database: `{env['benchmark_config']['database_name']}`

## Method

The Web API accepted all requests while no Outbox dispatcher or Temporal Web Worker was running
in the isolated benchmark namespace. Each HTTP request used the real API middleware, command
service, and PostgreSQL transaction. The runner verified queued Runs and pending `start_run`
events before starting a real replacement Worker subprocess. That process used the production
Outbox claim/ack code, deterministic Workflow IDs, real Temporal workflows, and benchmark-only
Activities that complete Runs without an external model provider.

## Results

- Accepted requests: **{summary['accepted_requests']}**
- Persisted requests: **{summary['persisted_requests']}**
- Eventually processed Runs: **{summary['eventually_processed_runs']}**
- Temporal completed Workflows: **{summary['temporal_completed_workflows']}**
- Terminal completed Runs: **{summary['terminal_completed_runs']}**
- Lost Runs: **{summary['lost_run_count']}**
- Duplicate Workflows: **{summary['duplicate_workflow_count']}**
- Outbox recovery success rate: **{summary['outbox_recovery_success_rate']:.2%}**
- Backlog drain time: **{summary['backlog_drain_time_ms']} ms**
- Eventual completion latency P50: **{latency['p50']} ms**
- Eventual completion latency P95: **{latency['p95']} ms**

## Failure Analysis

Observed failure count: **{len(summary['failures'])}**. Failure details are retained in the JSON
summary and per-request CSV rows.

## Limitations

HTTP was exercised through Starlette's in-process `TestClient`, not an external network socket.
Authentication used a benchmark credential adapter. All Web API routing, CSRF/idempotency checks,
PostgreSQL transactions, Outbox consumption, deterministic Workflow start, Temporal execution,
and Run lifecycle commits were real. The Agent Activity was intentionally fake, so this experiment
measures Outbox/orchestration recovery rather than model-provider reliability. Dispatcher
claim-during-crash injection (experiment B) remains optional and is not included in these numbers.
Post-completion `retain_memory` and `publish_terminal_event` consumers were intentionally not
started; their newly emitted events remain pending and are outside the measured `start_run` backlog.

## Reproduction

```bash
docker compose up -d app-postgres temporal-postgres temporal
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 \\
  TEMPORAL_NAMESPACE=hpagent-outbox-benchmark \\
  .venv/bin/python scripts/benchmarks/outbox_recovery_benchmark.py --requests 30
```
"""
    (output_dir / "outbox_recovery_report.md").write_text(report, encoding="utf-8")


async def async_main(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    run_dir = output_dir / "outbox_recovery_runs"
    if run_dir.exists():
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True)
    migration_url, app_url, worker_url = ensure_benchmark_database(
        args.admin_database_url, args.database_name
    )
    reset_benchmark_data(migration_url)
    seed_identity(migration_url)
    config = {
        "requests": args.requests,
        "temporal_host": args.temporal_host,
        "temporal_namespace": args.temporal_namespace,
        "database_name": args.database_name,
        "timeout_seconds": args.timeout,
        "worker_process": str(WORKER_HARNESS.relative_to(ROOT)),
        "fake_model": True,
    }
    env = environment(config)
    records = await asyncio.to_thread(accept_requests, app_url, worker_url, args.requests)
    await asyncio.to_thread(load_initial_facts, migration_url, records)
    pending = sum(record["outbox_pending_while_worker_down"] for record in records)
    print(f"Worker down: accepted={len(records)} pending_start_run={pending}", flush=True)
    ready_file = run_dir / "replacement.ready"
    worker: asyncio.subprocess.Process | None = None
    backlog_started: float | None = None
    try:
        worker = await start_worker(
            args.temporal_host,
            args.temporal_namespace,
            worker_url,
            ready_file,
            args.timeout,
        )
        backlog_started = time.monotonic()
        await wait_for_completion(migration_url, args.requests, args.timeout)
        await wait_for_temporal_results(
            args.temporal_host,
            args.temporal_namespace,
            records,
            args.timeout,
        )
        backlog_drain_ms = (time.monotonic() - backlog_started) * 1000
    except Exception as exc:
        backlog_drain_ms = (
            (time.monotonic() - backlog_started) * 1000 if backlog_started else 0.0
        )
        for record in records:
            if not record["error_type"]:
                record["error_type"] = type(exc).__name__
                record["error_message"] = str(exc)[:2000]
    finally:
        await terminate(worker)
    await asyncio.to_thread(load_final_facts, migration_url, records)
    summary = summarize(records, env, backlog_drain_ms)
    write_outputs(output_dir, records, summary)
    print(
        json.dumps(
            {
                "accepted_requests": summary["accepted_requests"],
                "persisted_requests": summary["persisted_requests"],
                "eventually_processed_runs": summary["eventually_processed_runs"],
                "temporal_completed_workflows": summary[
                    "temporal_completed_workflows"
                ],
                "lost_run_count": summary["lost_run_count"],
                "duplicate_workflow_count": summary["duplicate_workflow_count"],
                "outbox_recovery_success_rate": summary["outbox_recovery_success_rate"],
                "backlog_drain_time_ms": summary["backlog_drain_time_ms"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if not summary["failures"] else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=30)
    parser.add_argument("--temporal-host", default=os.getenv("TEMPORAL_HOST", "localhost:7233"))
    parser.add_argument(
        "--temporal-namespace",
        default=os.getenv("TEMPORAL_NAMESPACE", "hpagent-outbox-benchmark"),
    )
    parser.add_argument(
        "--admin-database-url",
        default=os.getenv(
            "OUTBOX_BENCHMARK_ADMIN_DATABASE_URL",
            "postgresql://hpagent_migrate:hpagent_migrate@localhost:5434/postgres",
        ),
    )
    parser.add_argument("--database-name", default="hpagent_outbox_benchmark")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--output-dir", default=str(ROOT / "artifacts" / "benchmarks"))
    args = parser.parse_args()
    if args.requests < 1:
        parser.error("--requests must be at least 1")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if not args.database_name.startswith("hpagent_") or not args.database_name.endswith(
        "_benchmark"
    ):
        parser.error("--database-name must be explicitly scoped as hpagent_*_benchmark")
    return args


def main() -> None:
    raise SystemExit(asyncio.run(async_main(parse_args())))


if __name__ == "__main__":
    main()
