"""Real-process Temporal replay acceptance for Durable ReAct and Plan."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Replayer

from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AgentRunInput,
    ChatContext,
    RunContext,
    RunSource,
)
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE, RunLifecycleInput

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]

_HARNESS = Path(__file__).parent / "support" / "durable_worker_process.py"


def _request(strategy: str) -> AgentRunInput:
    run_id = str(uuid4())
    return AgentRunInput(
        schema_version=AGENT_SCHEMA_VERSION,
        run_id=run_id,
        account_id=str(uuid4()),
        strategy=strategy,
        max_turns=3,
        source=RunSource("chat", str(uuid4())),
        context=RunContext(
            chat=ChatContext(str(uuid4()), str(uuid4()), str(uuid4())), surface="web"
        ),
    )


async def _wait_for(path: Path, timeout: float = 20) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.05)


async def _start_worker(
    role: str,
    host: str,
    namespace: str,
    state_dir: Path,
    ready_name: str,
) -> asyncio.subprocess.Process:
    environment = os.environ.copy()
    source_path = str(Path(__file__).parents[1] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_path, environment.get("PYTHONPATH", "")) if item
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(_HARNESS),
        role,
        host,
        namespace,
        str(state_dir),
        ready_name,
        env=environment,
    )
    await _wait_for(state_dir / f"{ready_name}.ready")
    assert process.returncode is None
    return process


async def _terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    # SIGKILL is intentional here: graceful Worker shutdown would not exercise
    # Temporal's lost-Worker recovery path and can wait for long poll shutdown.
    process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)


def _counts(state_dir: Path) -> tuple[dict[str, int], dict[str, int], int]:
    with sqlite3.connect(state_dir / "operations.sqlite") as connection:
        operations = dict(
            connection.execute(
                "SELECT kind, count(*) FROM operations GROUP BY kind"
            ).fetchall()
        )
        attempts = dict(
            connection.execute(
                "SELECT kind, count(*) FROM attempts GROUP BY kind"
            ).fetchall()
        )
        side_effects = connection.execute("SELECT count(*) FROM side_effects").fetchone()[0]
    return operations, attempts, side_effects


@pytest.mark.parametrize(
    ("strategy", "boundary", "expected_operations", "expected_version"),
    [
        (
            "react",
            "react-tool",
            {"context": 1, "model": 2, "tool": 1},
            4,
        ),
        (
            "plan_and_execute",
            "plan-step-2",
            {"context": 1, "planning": 1, "model": 3, "evaluation": 2},
            5,
        ),
    ],
)
async def test_workflow_process_kill_replays_without_repeating_completed_operations(
    tmp_path,
    strategy,
    boundary,
    expected_operations,
    expected_version,
):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required for real worker-kill acceptance")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(host, namespace=namespace)
    activity_worker = workflow_worker = replacement_worker = None
    lifecycle_worker = lifecycle_replacement = None
    try:
        activity_worker = await _start_worker(
            "activity", host, namespace, tmp_path, "activity"
        )
        workflow_worker = await _start_worker(
            "workflow", host, namespace, tmp_path, "workflow-1"
        )
        request = _request(strategy)
        (tmp_path / "run-input.json").write_text(json.dumps(asdict(request)))
        lifecycle_worker = await _start_worker("lifecycle", host, namespace, tmp_path, "lifecycle-1")
        handle = await client.start_workflow(
            AgentLifecycleWorkflow.run,
            RunLifecycleInput(1, request.run_id),
            id=f"worker-kill-{request.run_id}",
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        )
        await _wait_for(tmp_path / f"{boundary}.boundary")

        # This is an actual OS process termination. The Activity result is
        # released only after the Workflow Worker is confirmed dead, so the
        # replacement must rebuild state from Temporal History.
        await _terminate(workflow_worker)
        await _terminate(lifecycle_worker)
        (tmp_path / f"{boundary}.release").touch()
        replacement_worker = await _start_worker(
            "workflow", host, namespace, tmp_path, "workflow-2"
        )
        lifecycle_replacement = await _start_worker("lifecycle", host, namespace, tmp_path, "lifecycle-2")
        result = await asyncio.wait_for(handle.result(), timeout=30)
        assert result["outcome"] == "completed"
        await Replayer(workflows=[AgentLifecycleWorkflow]).replay_workflow(await handle.fetch_history())

        operations, attempts, side_effects = _counts(tmp_path)
        assert operations == expected_operations
        assert attempts == expected_operations
        assert side_effects == (1 if strategy == "react" else 0)
        if strategy == "plan_and_execute":
            with sqlite3.connect(tmp_path / "operations.sqlite") as connection:
                assert connection.execute(
                    "SELECT DISTINCT plan_version FROM operations "
                    "WHERE plan_version IS NOT NULL"
                ).fetchall() == [(1,)]
    finally:
        await _terminate(lifecycle_replacement)
        await _terminate(lifecycle_worker)
        await _terminate(replacement_worker)
        await _terminate(workflow_worker)
        await _terminate(activity_worker)
