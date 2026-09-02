from __future__ import annotations

import os
from uuid import uuid4

import pytest
from google.protobuf.duration_pb2 import Duration
from temporalio import activity
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Replayer, Worker

from orchestration.research_workflow import (
    ResearchIterationInput,
    ResearchReportWorkflow,
    ResearchWorkflowInput,
)
from orchestration.web_dispatcher import TemporalClientAdapter
from orchestration.web_workflow import WEB_LIFECYCLE_TASK_QUEUE

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]

stages: list[str] = []
sufficient_after = 1


def _result(request, stage: str, count: int = 1, *, sufficient: bool | None = None):
    stages.append(stage)
    result = {
        "schema_version": 1,
        "run_id": request.run_id,
        "stage": stage,
        "ref": f"postgres://stage/{request.run_id}:{stage}",
        "item_count": count,
    }
    if sufficient is not None:
        result["sufficient"] = sufficient
    return result


@activity.defn(name="prepare_research_activity")
async def prepare(request: ResearchWorkflowInput):
    return _result(request, "Planning")


@activity.defn(name="create_research_plan_activity")
async def plan(request: ResearchWorkflowInput):
    return _result(request, "SourceStrategy")


@activity.defn(name="discover_research_sources_activity")
async def discover(request: ResearchIterationInput):
    return _result(request, f"SourceDiscovery:{request.iteration}", 2)


@activity.defn(name="rank_research_sources_activity")
async def rank(request: ResearchIterationInput):
    return _result(request, f"SourceRanking:{request.iteration}", 2)


@activity.defn(name="fetch_research_sources_activity")
async def fetch(request: ResearchIterationInput):
    return _result(request, f"Fetch:{request.iteration}", 2)


@activity.defn(name="normalize_research_sources_activity")
async def normalize(request: ResearchIterationInput):
    return _result(request, f"NormalizeDeduplicate:{request.iteration}", 2)


@activity.defn(name="extract_research_evidence_activity")
async def evidence(request: ResearchIterationInput):
    return _result(request, f"EvidenceExtraction:{request.iteration}", 3)


@activity.defn(name="assess_research_corroboration_activity")
async def corroboration(request: ResearchIterationInput):
    return _result(request, f"Corroboration:{request.iteration}", 2)


@activity.defn(name="analyze_research_gaps_activity")
async def gaps(request: ResearchIterationInput):
    return _result(
        request,
        f"GapAnalysis:{request.iteration}",
        3,
        sufficient=request.iteration >= sufficient_after,
    )


@activity.defn(name="synthesize_research_report_activity")
async def synthesize(request: ResearchWorkflowInput):
    return _result(request, "Synthesis", 1)


@activity.defn(name="verify_research_citations_activity")
async def verify(request: ResearchWorkflowInput):
    return _result(request, "CitationVerification", 1)


@activity.defn(name="compare_previous_research_activity")
async def compare(request: ResearchWorkflowInput):
    return _result(request, "DailyDiff", 1)


@activity.defn(name="publish_research_artifact_activity")
async def publish(request: ResearchWorkflowInput):
    return _result(request, "PublishArtifact", 1)


@activity.defn(name="complete_research_activity")
async def complete(request: ResearchWorkflowInput):
    return _result(request, "Complete", 1)


@activity.defn(name="fail_research_activity")
async def fail(request: ResearchWorkflowInput):
    return _result(request, "Failed", 0)


@pytest.mark.parametrize("stop_after", [1, 4])
async def test_real_temporal_research_workflow_replays_with_compact_refs(stop_after):
    global sufficient_after
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")
    stages.clear()
    sufficient_after = stop_after
    namespace = os.getenv("TEMPORAL_TEST_NAMESPACE", "hpagent-research-test")
    bootstrap = await Client.connect(temporal_host)
    try:
        await bootstrap.service_client.workflow_service.register_namespace(
            RegisterNamespaceRequest(
                namespace=namespace,
                workflow_execution_retention_period=Duration(seconds=86400),
            )
        )
    except RPCError as exc:
        if exc.status != RPCStatusCode.ALREADY_EXISTS:
            raise
    client = await Client.connect(temporal_host, namespace=namespace)
    worker = Worker(
        client,
        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[ResearchReportWorkflow],
        activities=[
            prepare,
            plan,
            discover,
            rank,
            fetch,
            normalize,
            evidence,
            corroboration,
            gaps,
            synthesize,
            verify,
            compare,
            publish,
            complete,
            fail,
        ],
    )
    run_id = str(uuid4())
    workflow_id = f"hpagent-research-{run_id}"
    async with worker:
        temporal_run_id = await TemporalClientAdapter(client).start_research_run(
            workflow_id, ResearchWorkflowInput(1, run_id)
        )
        handle = client.get_workflow_handle(workflow_id, run_id=temporal_run_id)
        result = await handle.result()
        history = await handle.fetch_history()
    replay = await Replayer(workflows=[ResearchReportWorkflow]).replay_workflow(history)
    assert replay.replay_failure is None
    assert result == {"schema_version": 1, "run_id": run_id, "outcome": "report_published"}
    expected = [
        "Planning",
        "SourceStrategy",
    ]
    for iteration in range(1, 2 if stop_after == 1 else 4):
        expected.extend([
            f"SourceDiscovery:{iteration}", f"SourceRanking:{iteration}",
            f"Fetch:{iteration}", f"NormalizeDeduplicate:{iteration}",
            f"EvidenceExtraction:{iteration}", f"Corroboration:{iteration}",
            f"GapAnalysis:{iteration}",
        ])
    expected.extend([
        "Synthesis", "CitationVerification", "DailyDiff", "PublishArtifact", "Complete"
    ])
    assert stages == expected
    assert not any(stage.endswith(":4") for stage in stages)
