from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import ModuleType

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str) -> ModuleType:
    path = ROOT / "scripts" / "benchmarks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_is_balanced_and_preflight_only() -> None:
    runner = load_script("agent_strategy_benchmark")
    manifest = runner.load_manifest(runner.DEFAULT_MANIFEST)
    assert len(manifest["tasks"]) == 30
    assert [task["level"] for task in manifest["tasks"]].count("simple") == 10
    assert [task["level"] for task in manifest["tasks"]].count("medium") == 10
    assert [task["level"] for task in manifest["tasks"]].count("complex") == 10


def test_tag_grader_is_deterministic(tmp_path: Path) -> None:
    runner = load_script("agent_strategy_benchmark")
    task = {"grader": {"type": "tag_equals", "value": "alpha,beta"}}
    assert runner.grade(task, tmp_path, "Result: <answer> alpha,beta </answer>") == (
        True,
        None,
    )
    success, reason = runner.grade(task, tmp_path, "alpha,beta")
    assert not success
    assert reason == "missing_answer_tag"


def test_all_complex_fixtures_begin_with_a_real_failing_test(tmp_path: Path) -> None:
    runner = load_script("agent_strategy_benchmark")
    manifest = runner.load_manifest(runner.DEFAULT_MANIFEST)
    for task in (item for item in manifest["tasks"] if item["level"] == "complex"):
        task_dir = tmp_path / task["id"]
        task_dir.mkdir()
        for name, content in task["files"].items():
            destination = task_dir / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(content, encoding="utf-8")
        success, _ = runner.grade(task, task_dir, "")
        assert not success, f"{task['id']} must not pass before the Agent fixes it"


def test_resume_ignores_interrupted_audit_record(tmp_path: Path) -> None:
    runner = load_script("agent_strategy_benchmark")
    output = tmp_path / "trials.jsonl"
    rows = [
        {
            "task_id": "simple_001",
            "strategy": "react",
            "repetition": 1,
            "failure_type": None,
            "run_status": "completed",
        },
        {
            "task_id": "simple_001",
            "strategy": "plan_and_execute",
            "repetition": 1,
            "failure_type": "environment_error",
            "run_status": None,
        },
    ]
    output.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )

    assert runner.completed_keys(output) == {("simple_001", "react", 1)}


def test_prepare_session_preserves_managed_interruption(tmp_path: Path) -> None:
    runner = load_script("agent_strategy_benchmark")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=workspace, check=True, capture_output=True, text=True
        )

    run("init", "-b", "master")
    run("config", "user.name", "Benchmark Test")
    run("config", "user.email", "benchmark@example.invalid")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-m", "root")
    run("checkout", "-b", "hpagent/interrupted")
    stale = workspace / "agent_strategy_benchmark" / "stale" / "fixture.txt"
    stale.parent.mkdir(parents=True)
    stale.write_text("preserve me\n", encoding="utf-8")

    base = runner.prepare_session_branch(workspace, "new-session")

    assert base == "master"
    assert runner.git(workspace, "branch", "--show-current").stdout.strip() == (
        "hpagent/new-session"
    )
    assert not runner.git(workspace, "status", "--porcelain").stdout.strip()
    preserved = runner.git(
        workspace,
        "show",
        "hpagent/interrupted:agent_strategy_benchmark/stale/fixture.txt",
    )
    assert preserved.stdout == "preserve me\n"


def test_prepare_session_rejects_changes_outside_managed_directory(
    tmp_path: Path,
) -> None:
    runner = load_script("agent_strategy_benchmark")
    workspace = tmp_path / "repo"
    workspace.mkdir()
    subprocess.run(
        ["git", "init", "-b", "master"],
        cwd=workspace,
        check=True,
        capture_output=True,
        text=True,
    )
    (workspace / "user-file.txt").write_text("do not touch\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="outside the managed benchmark directory"):
        runner.preserve_interrupted_workspace(workspace)

    assert (workspace / "user-file.txt").read_text(encoding="utf-8") == "do not touch\n"


def test_finalize_session_reports_untracked_file_outside_task(tmp_path: Path) -> None:
    runner = load_script("agent_strategy_benchmark")
    workspace = tmp_path / "repo"
    workspace.mkdir()

    def run(*args: str) -> None:
        subprocess.run(
            ["git", *args], cwd=workspace, check=True, capture_output=True, text=True
        )

    run("init", "-b", "master")
    run("config", "user.name", "Benchmark Test")
    run("config", "user.email", "benchmark@example.invalid")
    (workspace / "README.md").write_text("fixture\n", encoding="utf-8")
    run("add", "README.md")
    run("commit", "-m", "root")
    run("checkout", "-b", "hpagent/trial")
    task_dir = workspace / "agent_strategy_benchmark" / "trial" / "complex_007"
    task_dir.mkdir(parents=True)
    (task_dir / "dates.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "dates.py").write_text("VALUE = 2\n", encoding="utf-8")

    changes = runner.finalize_session_branch(workspace, "master", task_dir)

    assert changes == ["dates.py"]


def test_summary_supersedes_interrupted_record_with_retry(tmp_path: Path) -> None:
    summary_script = load_script("summarize_agent_strategy_benchmark")
    output = tmp_path / "trials.jsonl"
    interrupted = {
        "task_id": "medium_006",
        "strategy": "plan_and_execute",
        "repetition": 1,
        "failure_type": "environment_error",
        "run_status": None,
    }
    completed = {
        **interrupted,
        "failure_type": None,
        "run_status": "completed",
        "success": True,
    }
    output.write_text(
        json.dumps(interrupted) + "\n" + json.dumps(completed) + "\n",
        encoding="utf-8",
    )

    assert summary_script.load(output) == [completed]


def test_summary_still_rejects_duplicate_completed_records(tmp_path: Path) -> None:
    summary_script = load_script("summarize_agent_strategy_benchmark")
    output = tmp_path / "trials.jsonl"
    completed = {
        "task_id": "simple_001",
        "strategy": "react",
        "repetition": 1,
        "failure_type": None,
        "run_status": "completed",
    }
    output.write_text(
        json.dumps(completed) + "\n" + json.dumps(completed) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate completed"):
        summary_script.load(output)


def test_pilot_retries_only_latest_transient_model_failures() -> None:
    experiment = load_script("agent_strategy_experiment")
    rows = [
        {"strategy": "react", "success": False, "failure_type": "model_error"},
        {"strategy": "plan_and_execute", "success": True, "failure_type": None},
    ]
    assert experiment.retryable_pilot_strategies(rows) == ["react"]

    rows.append({"strategy": "react", "success": True, "failure_type": None})
    assert experiment.retryable_pilot_strategies(rows) == []
    assert experiment.latest_pilot_rows(rows) == [rows[-1], rows[1]]


def test_pilot_does_not_retry_non_model_failure() -> None:
    experiment = load_script("agent_strategy_experiment")
    rows = [
        {"strategy": "react", "success": False, "failure_type": "incorrect_result"},
        {
            "strategy": "plan_and_execute",
            "success": False,
            "failure_type": "environment_error",
        },
    ]
    assert experiment.retryable_pilot_strategies(rows) == []


def test_api_check_retries_transient_readiness_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    experiment = load_script("agent_strategy_experiment")
    attempts = 0

    def fake_get(url: str, timeout: int) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        request = httpx.Request("GET", url)
        return httpx.Response(502 if attempts < 3 else 200, request=request)

    monkeypatch.setattr(experiment.httpx, "get", fake_get)
    monkeypatch.setattr(experiment.time, "sleep", lambda _seconds: None)

    assert experiment.api_check() == (True, "HpAgent API ready")
    assert attempts == 3


def test_summary_requires_balanced_design() -> None:
    summary_script = load_script("summarize_agent_strategy_benchmark")
    environment = {
        "model": "fixture-model",
        "provider": "fixture-provider",
        "temperature": 0,
        "max_turns": 6,
        "manifest_sha256": "abc",
    }
    rows = []
    for level in ("simple", "medium", "complex"):
        for index in range(1, 11):
            for strategy in ("react", "plan_and_execute"):
                rows.append(
                    {
                        "task_id": f"{level}_{index:03d}",
                        "level": level,
                        "strategy": strategy,
                        "repetition": 1,
                        "success": True,
                        "latency_ms": 100,
                        "model_calls": 1,
                        "tool_calls": 1,
                        "planning_calls": int(strategy == "plan_and_execute"),
                        "turns": 1,
                        "total_tokens": None,
                        "failure_type": None,
                        "environment": environment,
                    }
                )
    summary = summary_script.build_summary(rows, allow_partial=False)
    assert summary["design"]["complete_balanced_design"] is True
    assert summary["overall"]["react"]["trials"] == 30
    assert summary["paired_outcomes"]["complex"]["both_success"] == 10


def test_summary_excludes_environment_failures_from_evaluable_metrics() -> None:
    summary_script = load_script("summarize_agent_strategy_benchmark")
    rows = [
        {"success": True, "latency_ms": 100, "failure_type": None, "model_calls": 2},
        {
            "success": False,
            "latency_ms": 1,
            "failure_type": "environment_error",
            "model_calls": None,
        },
    ]

    result = summary_script.metrics(rows)

    assert result["trials"] == 2
    assert result["evaluable_trials"] == 1
    assert result["evaluable_success_rate"] == 1.0
    assert result["latency_ms"]["average"] == 100.0
