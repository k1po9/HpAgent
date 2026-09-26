"""Real Worker SIGKILL after durable publish/save commits and replacement recovery."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client

from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchReportWorkflow, ResearchWorkflowInput
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from research_domain.services import ResearchTaskCommandService
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog

from .test_workspace_v41_p4 import Canonicalizer, Content, Discovery, Synthesis, execute

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]
HARNESS = Path(__file__).parents[1] / "support" / "research_publish_save_kill_worker.py"


async def _wait_file(path: Path, timeout: float = 90) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.1)


async def _worker(role: str, phase: str, state: Path, env: dict[str, str]):
    process = await asyncio.create_subprocess_exec(
        sys.executable, str(HARNESS), role, phase, str(state), env=env,
    )
    await _wait_file(state / f"{role}.ready")
    return process


@pytest.mark.parametrize("phase", ["publish", "save"])
async def test_research_worker_sigkill_preserves_history_intent_and_results(
    phase, database_url, worker_database_url, account_id, tmp_path,
):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    namespace = os.getenv("TEMPORAL_TEST_NAMESPACE", "hpagent-research-sigkill")
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    target = catalog.create_directory(account_id, parent, f"sigkill-{phase}")
    commands = ResearchTaskCommandService(database_url)
    task_id = UUID(commands.create_task(account_id, str(uuid4()), "Daily", "Track facts",
                                        output_directory_id=target,
                                        output_required=True).body["task_id"])
    store_root = tmp_path / "store"
    store = TenantFileStore(store_root, max_bytes=1024 * 1024)
    baseline_activities = ResearchActivities(
        worker_database_url, Discovery(), Content(), Canonicalizer(), Synthesis(),
        min_evidence=1, min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(OutputPublisher(worker_database_url, store)),
    )
    prior = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    await execute(baseline_activities, prior)
    run_id = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    with UnitOfWork(database_url) as uow:
        before_intent = uow.execute(
            "SELECT operation_id,target_directory_id,required FROM research_run_save_intents "
            "WHERE run_id=%s", (run_id,),
        ).fetchone()
    assert before_intent is not None
    state = tmp_path / "worker-state"
    state.mkdir()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).parents[2] / "src")
    env["WORKER_DATABASE_URL"] = worker_database_url
    env["FILE_STORE_ROOT"] = str(store_root)
    env["TEMPORAL_HOST"] = host
    env["TEMPORAL_TEST_NAMESPACE"] = namespace
    client = await Client.connect(host, namespace=namespace)
    first = replacement = None
    try:
        first = await _worker("first", phase, state, env)
        handle = await client.start_workflow(
            ResearchReportWorkflow.run, ResearchWorkflowInput(1, str(run_id)),
            id=f"workspace-sigkill-{phase}-{run_id}", task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        )
        await _wait_file(state / f"{phase}.committed")
        assert await asyncio.wait_for(first.wait(), 10) == -9
        with UnitOfWork(database_url) as uow:
            baseline_at_kill = uow.execute(
                "SELECT selected FROM research_history_baselines WHERE run_id=%s", (run_id,),
            ).fetchone()["selected"]
            assert baseline_at_kill and baseline_at_kill[0]["run_id"] == str(prior)
            publish_operations_at_kill = [row["operation_id"] for row in uow.execute(
                "SELECT operation_id FROM output_publish_operations WHERE run_id=%s "
                "ORDER BY operation_id", (run_id,),
            ).fetchall()]
            assert len(publish_operations_at_kill) == 1
        replacement = await _worker("replacement", phase, state, env)
        result = await asyncio.wait_for(handle.result(), 120)
        assert result["outcome"] == "report_published"
        with UnitOfWork(database_url) as uow:
            after_intent = uow.execute(
                "SELECT operation_id,target_directory_id,required,state,entry_id "
                "FROM research_run_save_intents WHERE run_id=%s", (run_id,),
            ).fetchone()
            history = uow.execute(
                "SELECT selected FROM research_history_baselines WHERE run_id=%s", (run_id,),
            ).fetchone()["selected"]
            report_count = uow.execute(
                "SELECT count(*) AS n FROM research_reports WHERE run_id=%s", (run_id,),
            ).fetchone()["n"]
            output_count = uow.execute(
                "SELECT count(*) AS n FROM output_publish_operations WHERE run_id=%s "
                "AND status='completed'", (run_id,),
            ).fetchone()["n"]
            publish_operations_after = [row["operation_id"] for row in uow.execute(
                "SELECT operation_id FROM output_publish_operations WHERE run_id=%s "
                "ORDER BY operation_id", (run_id,),
            ).fetchall()]
            entry_count = uow.execute(
                "SELECT count(*) AS n FROM workspace_save_operations WHERE operation_id=%s",
                (before_intent["operation_id"],),
            ).fetchone()["n"]
        assert history == baseline_at_kill
        assert publish_operations_after == publish_operations_at_kill
        assert (after_intent["operation_id"], after_intent["target_directory_id"],
                after_intent["required"]) == (
                    before_intent["operation_id"], before_intent["target_directory_id"],
                    before_intent["required"],
                )
        assert after_intent["state"] == "succeeded" and after_intent["entry_id"] is not None
        assert (report_count, output_count, entry_count) == (1, 1, 1)
    finally:
        for process in (replacement, first):
            if process is not None and process.returncode is None:
                process.kill()
                await process.wait()
