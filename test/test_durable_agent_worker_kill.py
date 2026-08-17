"""Real-process Temporal replay acceptance for Durable ReAct and Plan."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.contracts import AGENT_SCHEMA_VERSION, AGENT_TASK_QUEUE, AgentRunInput

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]

_HARNESS = Path(__file__).parent / "support" / "durable_worker_process.py"


def _request(strategy: str) -> AgentRunInput:
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
    try:
        activity_worker = await _start_worker(
            "activity", host, namespace, tmp_path, "activity"
        )
        workflow_worker = await _start_worker(
            "workflow", host, namespace, tmp_path, "workflow-1"
        )
        request = _request(strategy)
        handle = await client.start_workflow(
            AgentRunWorkflow.run,
            request,
            id=f"worker-kill-{request.run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )
        await _wait_for(tmp_path / f"{boundary}.boundary")

        # This is an actual OS process termination. The Activity result is
        # released only after the Workflow Worker is confirmed dead, so the
        # replacement must rebuild state from Temporal History.
        await _terminate(workflow_worker)
        (tmp_path / f"{boundary}.release").touch()
        replacement_worker = await _start_worker(
            "workflow", host, namespace, tmp_path, "workflow-2"
        )
        result = await asyncio.wait_for(handle.result(), timeout=30)
        assert result.transcript_version == expected_version

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
        await _terminate(replacement_worker)
        await _terminate(workflow_worker)
        await _terminate(activity_worker)
