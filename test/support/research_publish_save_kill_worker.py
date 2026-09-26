"""Process-isolated real Temporal Worker for publication/save crash acceptance."""
from __future__ import annotations

import asyncio
import os
import signal
import sys
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchReportWorkflow
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
from research_activities import ResearchActivities
from research_domain.models import (
    ClaimDraft,
    ResearchReport,
    SourceCandidate,
    SourceContent,
    content_sha256,
)
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog


class Discovery:
    async def discover(self, query, *, strategy, limit):
        return [SourceCandidate("https://example.com/a", provider="fixture"),
                SourceCandidate("https://example.org/b", provider="fixture")][:limit]


class Canonicalizer:
    def canonicalize(self, uri):
        return uri


class Content:
    async def fetch(self, candidate):
        value = "fixed evidence " + candidate.uri
        return SourceContent(canonical_uri=candidate.uri, title="Fixture", text=value,
                             content_hash=content_sha256(value), mime_type="text/html")


class Synthesis:
    async def synthesize(self, objective, evidence):
        return ResearchReport("Fixture", "# Fixed report\n", (
            ClaimDraft("Fixed claim", (str(evidence[0]["evidence_id"]),)),
        ))


async def main() -> None:
    role, phase, state_root = sys.argv[1:4]
    state = Path(state_root)
    database = os.environ["WORKER_DATABASE_URL"]
    store = TenantFileStore(Path(os.environ["FILE_STORE_ROOT"]), max_bytes=1024 * 1024)
    publisher = OutputPublisher(database, store)
    if role == "first" and phase == "publish":
        original = publisher.publish

        def crash_after_publish(*args, **kwargs):
            result = original(*args, **kwargs)
            (state / "publish.committed").write_text(str(result.file_id))
            os.kill(os.getpid(), signal.SIGKILL)

        publisher.publish = crash_after_publish  # type: ignore[method-assign]
    if role == "first" and phase == "save":
        original_save = WorkspaceCatalog.save_file

        def crash_after_save(self, *args, **kwargs):
            result = original_save(self, *args, **kwargs)
            (state / "save.committed").write_text(str(result))
            os.kill(os.getpid(), signal.SIGKILL)

        WorkspaceCatalog.save_file = crash_after_save  # type: ignore[method-assign]
    activities = ResearchActivities(
        database, Discovery(), Content(), Canonicalizer(), Synthesis(),
        min_evidence=1, min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(publisher),
    )
    client = await Client.connect(os.environ["TEMPORAL_HOST"],
                                  namespace=os.environ["TEMPORAL_TEST_NAMESPACE"])
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
        (state / f"{role}.ready").write_text("ready")
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
