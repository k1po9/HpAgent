from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from orchestration.research_workflow import (
    ResearchIterationInput,
    ResearchReportWorkflow,
    ResearchTaskScheduleWorkflow,
)
from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE


def result(request, stage, *, sufficient=None):
    run_id = request["run_id"] if isinstance(request, dict) else request.run_id
    value = {
        "schema_version": 1, "run_id": run_id, "stage": stage,
        "ref": f"postgres://stage/{run_id}:{stage}", "item_count": 1,
    }
    if sufficient is not None:
        value["sufficient"] = sufficient
    return value


def simple(name, stage):
    @activity.defn(name=name)
    async def implementation(request):
        iteration = request.get("iteration") if isinstance(request, dict) else getattr(request, "iteration", None)
        suffix = f":{iteration}" if iteration is not None else ""
        return result(request, stage + suffix)
    return implementation


prepare = simple("prepare_research_activity", "Planning")
plan = simple("create_research_plan_activity", "SourceStrategy")
discover = simple("discover_research_sources_activity", "SourceDiscovery")
rank = simple("rank_research_sources_activity", "SourceRanking")
normalize = simple("normalize_research_sources_activity", "NormalizeDeduplicate")
evidence = simple("extract_research_evidence_activity", "EvidenceExtraction")
corroborate = simple("assess_research_corroboration_activity", "Corroboration")
synthesize = simple("synthesize_research_report_activity", "Synthesis")
verify = simple("verify_research_citations_activity", "CitationVerification")
compare = simple("compare_previous_research_activity", "DailyDiff")
publish = simple("publish_research_artifact_activity", "PublishArtifact")
complete = simple("complete_research_activity", "Complete")
fail = simple("fail_research_activity", "Failed")


async def main() -> None:
    role, host, namespace, state_value = sys.argv[1:5]
    state = Path(state_value)

    @activity.defn(name="fetch_research_sources_activity")
    async def fetch(request: ResearchIterationInput):
        attempts = state / "fetch.attempts"
        count = int(attempts.read_text() or "0") if attempts.exists() else 0
        attempts.write_text(str(count + 1))
        if role == "first":
            activity.heartbeat({"phase": "fetch"})
            (state / "fetch.boundary").touch()
            await asyncio.Future()
        return result(request, f"Fetch:{request.iteration}")

    @activity.defn(name="analyze_research_gaps_activity")
    async def gaps(request: ResearchIterationInput):
        return result(request, f"GapAnalysis:{request.iteration}", sufficient=True)

    @activity.defn(name="trigger_scheduled_research_activity")
    async def trigger_schedule(request: dict):
        return {"accepted": True, "run_id": request["fire_id"]}

    client = await Client.connect(host, namespace=namespace)
    worker = Worker(
        client,
        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[ResearchReportWorkflow, ResearchTaskScheduleWorkflow],
        activities=[
            prepare, plan, discover, rank, fetch, normalize, evidence, corroborate,
            gaps, synthesize, verify, compare, publish, complete, fail, trigger_schedule,
        ],
    )
    async with worker:
        (state / f"{role}.ready").touch()
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
