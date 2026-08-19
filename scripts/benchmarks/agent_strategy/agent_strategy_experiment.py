#!/usr/bin/env python3
"""One-command environment gate and execution orchestration for experiment 3."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
import secrets
import signal
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
import yaml

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / "config" / "config.yaml"
MODELS = ROOT / "config" / "models.yaml"
DOTENV = ROOT / ".env"
RUNTIME_ENV = ROOT / ".data" / "benchmarks" / "agent_strategy.env.json"
BENCHMARK_DIR = ROOT / "scripts" / "benchmarks" / "agent_strategy"
RUNNER = BENCHMARK_DIR / "agent_strategy_benchmark.py"
SUMMARIZER = BENCHMARK_DIR / "summarize_agent_strategy_benchmark.py"
ARTIFACTS = ROOT / "artifacts" / "benchmarks" / "agent_strategy"
PILOTS = ARTIFACTS / "pilots"
REQUIRED_SERVICES = {
    "app-postgres",
    "redis",
    "temporal",
    "hindsight",
    "hpagent",
    "hpagent-api",
}
PILOT_STRATEGIES = ("react", "plan_and_execute")
PILOT_MAX_ATTEMPTS = 3


def command(*args: str, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        list(args), cwd=ROOT, text=True, capture_output=True, check=False, env=env
    )
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"command failed: {args}")
    return result


def stream_command(*args: str, env: dict[str, str] | None = None) -> int:
    process = subprocess.Popen(list(args), cwd=ROOT, env=env)
    try:
        return process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            try:
                # The terminal normally delivers SIGINT to both parent and child.
                # Give the runner time to persist its interrupted audit record
                # before sending a fallback signal ourselves.
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.send_signal(signal.SIGINT)
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.wait(timeout=5)
        raise


def dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    if not DOTENV.exists():
        return values
    for raw in DOTENV.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def database_urls(values: dict[str, str]) -> tuple[str, str]:
    api_password = values.get("HPAGENT_API_PASSWORD") or "hpagent_api"
    worker_password = values.get("HPAGENT_WORKER_PASSWORD") or "hpagent_worker"
    return (
        f"postgresql://hpagent_api:{api_password}@127.0.0.1:5434/hpagent",
        f"postgresql://hpagent_worker:{worker_password}@127.0.0.1:5434/hpagent",
    )


def effective_config(values: dict[str, str]) -> dict[str, Any]:
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    models = yaml.safe_load(MODELS.read_text(encoding="utf-8")) or {}
    chat = list(models.get("chat") or [])
    if not chat:
        raise RuntimeError("config/models.yaml has no chat model")
    first = dict(chat[0])
    model_value = str(first.get("model", ""))
    if model_value.startswith("${") and model_value.endswith("}"):
        model_value = values.get(model_value[2:-1], "")
    provider = str(first.get("provider", ""))
    provider_config = dict((models.get("providers") or {}).get(provider) or {})
    base_value = str(provider_config.get("base_url", ""))
    if base_value.startswith("${") and base_value.endswith("}"):
        base_value = values.get(base_value[2:-1], "")
    return {
        "native_tools_enabled": bool(config.get("sandbox", {}).get("native_tools_enabled")),
        "durable_agent_enabled": values.get("DURABLE_AGENT_ENABLED", "false").lower() == "true",
        "max_turns": int(config.get("agent", {}).get("max_tool_turns", 5)),
        "provider": provider,
        "model": model_value,
        "provider_base_url_set": bool(base_value),
        "provider_key_set": bool(values.get("MINIMAX_API_KEY")) if provider == "minimax" else True,
        "origin": values.get("WEB_PUBLIC_ORIGIN") or "http://127.0.0.1:5173",
    }


def docker_services() -> tuple[bool, str]:
    result = command("docker", "compose", "--profile", "web", "ps", "--format", "json", check=False)
    if result.returncode:
        return False, result.stderr.strip() or "docker compose ps failed"
    found: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        found[str(item.get("Service"))] = str(item.get("Health") or item.get("State"))
    missing = sorted(REQUIRED_SERVICES - set(found))
    unhealthy = sorted(
        name for name in REQUIRED_SERVICES & set(found)
        if found[name].lower() not in {"healthy", "running"}
    )
    if missing or unhealthy:
        return False, f"missing={missing}, unhealthy={unhealthy}, observed={found}"
    return True, "required Docker services are running/healthy"


def provider_connectivity() -> tuple[bool, str]:
    probe = (
        "import json,os,time,httpx\n"
        "url=os.environ.get('MINIMAX_API_BASE_URL','').rstrip('/')\n"
        "out=[]\n"
        "for _ in range(3):\n"
        " t=time.monotonic()\n"
        " try:\n"
        "  r=httpx.get(url,timeout=10); out.append({'ok':True,'ms':round((time.monotonic()-t)*1000,1),'status':r.status_code})\n"
        " except Exception as e: out.append({'ok':False,'ms':round((time.monotonic()-t)*1000,1),'error':type(e).__name__})\n"
        "print(json.dumps(out))\n"
    )
    result = command(
        "docker", "compose", "exec", "-T", "hpagent", "python", "-c", probe, check=False
    )
    if result.returncode:
        return False, result.stderr.strip() or "container connectivity probe failed"
    try:
        attempts = json.loads(result.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        return False, f"invalid connectivity probe output: {exc}"
    ok = all(bool(item.get("ok")) for item in attempts)
    return ok, json.dumps(attempts, ensure_ascii=False)


def worker_loaded_current_config() -> tuple[bool, str]:
    container = command("docker", "compose", "ps", "-q", "hpagent", check=False)
    container_id = container.stdout.strip()
    if container.returncode or not container_id:
        return False, "hpagent container id unavailable"
    inspected = command(
        "docker",
        "inspect",
        "-f",
        "{{.State.StartedAt}}",
        container_id,
        check=False,
    )
    if inspected.returncode:
        return False, inspected.stderr.strip() or "docker inspect failed"
    try:
        started = datetime.fromisoformat(inspected.stdout.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        return False, f"invalid container StartedAt: {exc}"
    modified = datetime.fromtimestamp(CONFIG.stat().st_mtime, tz=UTC)
    ok = started >= modified
    detail = (
        "Worker started after current config was written"
        if ok
        else "config is newer than Worker; run: docker compose restart hpagent"
    )
    return ok, detail


def database_check(api_url: str, worker_url: str) -> tuple[bool, str]:
    try:
        for url in (api_url, worker_url):
            with psycopg.connect(url, connect_timeout=5) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                if cursor.fetchone() != (1,):
                    return False, "database SELECT 1 returned an unexpected result"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    return True, "API and Worker database roles connected"


def api_check() -> tuple[bool, str]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = httpx.get("http://127.0.0.1:8080/health/ready", timeout=5)
            response.raise_for_status()
            return True, "HpAgent API ready"
        except Exception as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2)
    assert last_error is not None
    return False, f"{type(last_error).__name__}: {last_error}"


def save_runtime(values: dict[str, str]) -> None:
    RUNTIME_ENV.parent.mkdir(parents=True, exist_ok=True)
    RUNTIME_ENV.write_text(json.dumps(values, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    RUNTIME_ENV.chmod(stat.S_IRUSR | stat.S_IWUSR)


def load_runtime() -> dict[str, str] | None:
    if not RUNTIME_ENV.exists():
        return None
    mode = stat.S_IMODE(RUNTIME_ENV.stat().st_mode)
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise RuntimeError(f"runtime credential file permissions must be 0600, got {oct(mode)}")
    return {key: str(value) for key, value in json.loads(RUNTIME_ENV.read_text(encoding="utf-8")).items()}


def login(runtime: dict[str, str]) -> tuple[bool, str]:
    try:
        with httpx.Client(base_url=runtime["HPAGENT_BENCHMARK_BASE_URL"], timeout=10) as client:
            response = client.post(
                "/auth/login",
                json={
                    "username": runtime["HPAGENT_BENCHMARK_USERNAME"],
                    "password": runtime["HPAGENT_BENCHMARK_PASSWORD"],
                    "return_to": "/",
                },
                follow_redirects=False,
            )
            if response.status_code != 303:
                return False, f"benchmark account login HTTP {response.status_code}"
            me = client.get("/api/v1/me")
            me.raise_for_status()
            payload = me.json()
            if not payload.get("capabilities", {}).get("durable_agent"):
                return False, "API reports durable_agent=false"
            if str(payload["account"]["account_id"]) != runtime["account_id"]:
                return False, "benchmark account identity mismatch"
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    workspace = Path(runtime["HPAGENT_BENCHMARK_WORKSPACE"])
    if not (workspace / ".git").exists() or workspace.parent.name != runtime["account_id"]:
        return False, "benchmark Git workspace is missing or account-scoped incorrectly"
    return True, f"dedicated benchmark account ready ({runtime['account_id']})"


def provision_runtime(config: dict[str, Any], api_url: str, worker_url: str) -> tuple[dict[str, str], str]:
    existing = load_runtime()
    if existing is not None:
        return existing, "reused existing 0600 benchmark runtime config"
    from sandbox.git_repo import GitRepoManager

    username = "agent-benchmark-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    password = secrets.token_urlsafe(32)
    with httpx.Client(base_url="http://127.0.0.1:8080", timeout=15) as client:
        response = client.post("/auth/register", json={"username": username, "password": password})
        response.raise_for_status()
        account_id = str(response.json()["account"]["account_id"])
    workspace_root = ROOT / ".data" / "workspace"
    asyncio.run(GitRepoManager(workspace_root).ensure_repo(account_id))
    runtime = {
        "HPAGENT_BENCHMARK_BASE_URL": "http://127.0.0.1:8080",
        "HPAGENT_BENCHMARK_ORIGIN": str(config["origin"]),
        "HPAGENT_BENCHMARK_USERNAME": username,
        "HPAGENT_BENCHMARK_PASSWORD": password,
        "HPAGENT_BENCHMARK_WORKSPACE": str((workspace_root / account_id / "repo").resolve()),
        "APP_DATABASE_URL": api_url,
        "WORKER_DATABASE_URL": worker_url,
        "account_id": account_id,
    }
    save_runtime(runtime)
    return runtime, "created dedicated benchmark account and 0600 runtime config"


def check_environment(*, provision: bool = True) -> tuple[bool, dict[str, Any], dict[str, str] | None]:
    values = dotenv()
    config = effective_config(values)
    api_url, worker_url = database_urls(values)
    checks: dict[str, dict[str, Any]] = {}

    def record(name: str, outcome: tuple[bool, str]) -> None:
        checks[name] = {"ok": outcome[0], "detail": outcome[1]}

    record("docker_services", docker_services())
    record("api_ready", api_check())
    record("database_roles", database_check(api_url, worker_url))
    record(
        "durable_agent_config",
        (config["durable_agent_enabled"], f"DURABLE_AGENT_ENABLED={config['durable_agent_enabled']}"),
    )
    record(
        "native_workspace_tools",
        (
            config["native_tools_enabled"],
            "native tools enabled" if config["native_tools_enabled"] else "set sandbox.native_tools_enabled: true and restart hpagent",
        ),
    )
    if checks["docker_services"]["ok"]:
        record("worker_loaded_current_config", worker_loaded_current_config())
    else:
        record("worker_loaded_current_config", (False, "skipped because hpagent is unavailable"))
    provider_config_ok = bool(config["model"] and config["provider_base_url_set"] and config["provider_key_set"])
    record("provider_config", (provider_config_ok, f"provider={config['provider']}, model={config['model'] or 'missing'}"))
    if checks["docker_services"]["ok"] and provider_config_ok:
        record("provider_connectivity_3x", provider_connectivity())
    else:
        record("provider_connectivity_3x", (False, "skipped because service/provider config failed"))
    runtime: dict[str, str] | None = None
    if provision and checks["api_ready"]["ok"] and checks["database_roles"]["ok"]:
        try:
            runtime, detail = provision_runtime(config, api_url, worker_url)
            account_outcome = login(runtime)
            record("benchmark_account", (account_outcome[0], f"{detail}; {account_outcome[1]}"))
        except Exception as exc:
            record("benchmark_account", (False, f"{type(exc).__name__}: {exc}"))
    else:
        record("benchmark_account", (False, "provisioning skipped"))
    ok = all(item["ok"] for item in checks.values())
    report = {
        "ready": ok,
        "will_call_model_api": False,
        "checked_at": datetime.now(UTC).isoformat(),
        "host": platform.platform(),
        "effective": {
            "provider": config["provider"],
            "model": config["model"],
            "temperature": "provider-default",
            "max_turns": config["max_turns"],
        },
        "checks": checks,
    }
    return ok, report, runtime


def print_report(report: dict[str, Any]) -> None:
    print("Agent strategy environment:", "PASS" if report["ready"] else "FAIL")
    for name, item in report["checks"].items():
        print(f"  [{'PASS' if item['ok'] else 'FAIL'}] {name}: {item['detail']}")
    print("  model API calls performed: 0")


def runtime_environment(runtime: dict[str, str]) -> dict[str, str]:
    result = dict(os.environ)
    result.update({key: value for key, value in runtime.items() if key != "account_id"})
    result["PYTHONPATH"] = "src"
    return result


def latest_pilot_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        strategy = str(row.get("strategy", ""))
        if strategy in PILOT_STRATEGIES:
            latest[strategy] = row
    return [latest[strategy] for strategy in PILOT_STRATEGIES if strategy in latest]


def retryable_pilot_strategies(rows: list[dict[str, Any]]) -> list[str]:
    return [
        str(row["strategy"])
        for row in latest_pilot_rows(rows)
        if not bool(row.get("success")) and row.get("failure_type") == "model_error"
    ]


def execute() -> None:
    ok, report, runtime = check_environment()
    print_report(report)
    if not ok or runtime is None:
        raise SystemExit("environment gate failed; formal experiment was not started")
    config = report["effective"]
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    pilot = PILOTS / f"gate_{stamp}.jsonl"
    pilot.parent.mkdir(parents=True, exist_ok=True)
    common = [
        sys.executable,
        str(RUNNER),
        "run",
        "--execute-model-api",
        "--model",
        str(config["model"]),
        "--provider",
        str(config["provider"]),
        "--temperature",
        str(config["temperature"]),
        "--max-turns",
        str(config["max_turns"]),
        "--timeout-seconds",
        "300",
    ]
    strategies = list(PILOT_STRATEGIES)
    rows: list[dict[str, Any]] = []
    for attempt in range(1, PILOT_MAX_ATTEMPTS + 1):
        if attempt > 1:
            print(
                f"Transient pilot model error; retry {attempt}/{PILOT_MAX_ATTEMPTS} "
                f"for: {', '.join(strategies)}",
                flush=True,
            )
        pilot_args = [
            *common,
            "--task-ids",
            "simple_001",
            "--strategies",
            *strategies,
            "--output",
            str(pilot),
            "--run-group",
            f"gate-{stamp}-a{attempt}",
        ]
        if attempt > 1:
            pilot_args.append("--no-resume")
        pilot_returncode = stream_command(
            *pilot_args,
            env=runtime_environment(runtime),
        )
        if pilot_returncode:
            raise SystemExit(f"pilot command failed; see {pilot}")
        rows = [
            json.loads(line)
            for line in pilot.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        strategies = retryable_pilot_strategies(rows)
        if not strategies or attempt == PILOT_MAX_ATTEMPTS:
            break
    rows = latest_pilot_rows(rows)
    gate_ok = (
        len(rows) == 2
        and all(bool(row.get("success")) for row in rows)
        and all(float(row.get("latency_ms", 999_999)) <= 180_000 for row in rows)
    )
    if not gate_ok:
        print(json.dumps({
            "pilot_passed": False,
            "records": [
                {
                    "strategy": row.get("strategy"),
                    "success": row.get("success"),
                    "latency_ms": row.get("latency_ms"),
                    "failure_type": row.get("failure_type"),
                }
                for row in rows
            ],
            "pilot_records": sum(
                1 for row in pilot.read_text(encoding="utf-8").splitlines() if row.strip()
            ),
            "formal_started": False,
            "pilot_file": str(pilot),
        }, ensure_ascii=False, indent=2))
        raise SystemExit("pilot gate failed; formal experiment was not started")
    formal = ARTIFACTS / "trials.jsonl"
    formal_returncode = stream_command(
        *common,
        "--output",
        str(formal),
        "--run-group",
        "formal-01",
        "--repetitions",
        "1",
        env=runtime_environment(runtime),
    )
    if formal_returncode:
        raise SystemExit(f"formal runner stopped; rerun this command to resume {formal}")
    summary = command(
        sys.executable,
        str(SUMMARIZER),
        "--input",
        str(formal),
        "--output-dir",
        str(ARTIFACTS),
        env=runtime_environment(runtime),
        check=False,
    )
    print(summary.stdout, end="")
    if summary.returncode:
        raise SystemExit(summary.stderr.strip() or "summary generation failed")
    print("Experiment complete. Review artifacts/benchmarks/agent_strategy/README.md")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "run"))
    args = parser.parse_args()
    if args.command == "check":
        ok, report, _ = check_environment()
        print_report(report)
        report_path = ARTIFACTS / "environment_check.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(0 if ok else 1)
    execute()


if __name__ == "__main__":
    main()
