"""Real Temporal Worker, Research Activities, PostgreSQL, and save retry."""
from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client
from temporalio.worker import Worker

from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchReportWorkflow, ResearchWorkflowInput
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from research_domain.services import ResearchTaskCommandService
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog

from .test_workspace_v41_p4 import Canonicalizer, Content, Discovery, Synthesis

pytestmark = [pytest.mark.postgres, pytest.mark.temporal, pytest.mark.asyncio]


async def test_real_temporal_activity_retry_resumes_save_only(
    database_url, worker_database_url, account_id, tmp_path, monkeypatch,
):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    target = catalog.create_directory(account_id, parent, "真实 Temporal")
    commands = ResearchTaskCommandService(database_url)
    task_id = UUID(commands.create_task(account_id, str(uuid4()), "Daily", "Track facts",
                                        output_directory_id=target,
                                        output_required=True).body["task_id"])
    run_id = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    synthesis = Synthesis()
    activities = ResearchActivities(worker_database_url, Discovery(), Content(),
        Canonicalizer(), synthesis, min_evidence=1, min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(OutputPublisher(
            worker_database_url, TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024))))
    original_save = WorkspaceCatalog.save_file
    attempts = 0

    def fail_once(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected save failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(WorkspaceCatalog, "save_file", fail_once)
    client = await Client.connect(host, namespace=os.getenv(
        "TEMPORAL_TEST_NAMESPACE", "hpagent-p4-20260926"))
    worker = Worker(client, task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[ResearchReportWorkflow], activities=[
            activities.prepare_research_activity,
            activities.create_research_plan_activity,
            activities.discover_research_sources_activity,
            activities.rank_research_sources_activity,
            activities.fetch_research_sources_activity,
            activities.normalize_research_sources_activity,
            activities.extract_research_evidence_activity,
            activities.assess_research_corroboration_activity,
            activities.analyze_research_gaps_activity,
            activities.synthesize_research_report_activity,
            activities.verify_research_citations_activity,
            activities.compare_previous_research_activity,
            activities.publish_research_artifact_activity,
            activities.save_research_workspace_activity,
            activities.complete_research_activity,
            activities.fail_research_activity,
        ])
    async with worker:
        result = await client.execute_workflow(ResearchReportWorkflow.run,
            ResearchWorkflowInput(1, str(run_id)), id=f"p4-actual-{run_id}",
            task_queue=WEB_LIFECYCLE_TASK_QUEUE)
    assert result["outcome"] == "report_published"
    assert attempts == 2 and len(synthesis.objectives) == 1
    with UnitOfWork(database_url) as uow:
        intent = uow.execute("SELECT state,entry_id FROM research_run_save_intents "
                             "WHERE run_id=%s", (run_id,)).fetchone()
        run = uow.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        assert intent["state"] == "succeeded" and intent["entry_id"] is not None
        assert run["status"] == "completed"
