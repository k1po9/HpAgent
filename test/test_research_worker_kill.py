from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from temporalio.client import Client

from orchestration.research_workflow import ResearchReportWorkflow, ResearchWorkflowInput
from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]
HARNESS = Path(__file__).parent / "support" / "research_worker_process.py"


async def wait_file(path: Path, timeout=30):
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.05)


async def start(role, host, namespace, state):
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(HARNESS), role, host, namespace, str(state), env=environment
    )
    await wait_file(state / f"{role}.ready")
    return process


async def kill(process):
    if process is not None and process.returncode is None:
        process.kill()
        await asyncio.wait_for(process.wait(), 5)


async def test_fetch_activity_worker_sigkill_retries_and_resumes_same_research_workflow(tmp_path):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required for real Research worker-kill acceptance")
    namespace = os.getenv("TEMPORAL_TEST_NAMESPACE", "hpagent-research-test")
    client = await Client.connect(host, namespace=namespace)
    first = replacement = None
    run_id = str(uuid4())
    workflow_id = f"research-worker-kill-{run_id}"
    try:
        first = await start("first", host, namespace, tmp_path)
        handle = await client.start_workflow(
            ResearchReportWorkflow.run, ResearchWorkflowInput(1, run_id),
            id=workflow_id, task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        )
        await wait_file(tmp_path / "fetch.boundary")
        await kill(first)
        replacement = await start("replacement", host, namespace, tmp_path)
        result = await asyncio.wait_for(handle.result(), timeout=45)
        assert result["run_id"] == run_id
        assert int((tmp_path / "fetch.attempts").read_text()) == 2
        description = await client.get_workflow_handle(workflow_id).describe()
        assert description.id == workflow_id
    finally:
        await kill(replacement)
        await kill(first)
