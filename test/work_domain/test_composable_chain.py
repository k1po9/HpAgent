"""Scripted decisions through production Temporal tools; no real Provider or outbound I/O."""

import asyncio
import json
import os
from uuid import UUID, uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker
from test_capability_facts import execution
from test_html_capability import Model
from test_research_capability import setup as research_setup

from agent_activities.segments import SegmentActivities
from agent_workflows.capability import CapabilityInvocationWorkflow
from agent_workflows.capability_contracts import CapabilityExecutionInput
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    CompactToolCall,
    RunContext,
    RunSource,
    ToolExecutionInput,
    ToolExecutionResult,
)
from agent_workflows.html import HtmlCapabilityWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow
from orchestration.research_workflow import ResearchCapabilityWorkflow
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE
from persistence.uow import UnitOfWork
from research_activities.composition import compose_research
from run_domain.acceptance import ResultAdoptionService
from run_domain.capability_contracts import AcceptancePredicate
from run_domain.lifecycle import RunLifecycleService
from run_domain.results import complete_operation
from web_artifacts.generator import WebArtifactGenerator
from web_artifacts.local import HtmlActivities
from work_domain.commands import WorkCommandService
from work_domain.execution_result import generic_result
from work_domain.models import Requirement
from work_domain.persistence import WorkRepository
from workspace.catalog import WorkspaceCatalog
from workspace.resources import ResourcePolicy

pytestmark = [pytest.mark.postgres, pytest.mark.temporal]


async def test_scripted_research_html_feedback_save_and_lost_reply(
    urls, owner, account_id, tmp_path
):
    catalog = WorkspaceCatalog(urls[1])
    directory = UUID(catalog.initialize(account_id)["root_id"])
    commands = WorkCommandService(urls[1])
    work = commands.accept(
        account_id,
        str(uuid4()),
        "Compose report",
        Requirement(
            "Research, produce HTML and save the explicitly requested revision",
            "generic_work",
            {"schema_version": 1},
            acceptance_criteria=(
                {"id": "html", "required": True, "evidence_types": ["check_passed"]},
                {"id": "save", "required": True, "evidence_types": ["workspace_saved"]},
            ),
            deliverable_policy={
                "schema_version": 1,
                "required": True,
                "directory_id": str(directory),
                "operation": "create_child",
            },
        ),
    ).body["work"]
    wid = UUID(work["work_id"])
    ResourcePolicy(urls[1]).grant(account_id, "work", wid, directory, ["create_child"], False)
    work = commands.get(account_id, wid)["work"]
    run = UUID(
        commands.advance(account_id, wid, str(uuid4()), work["row_version"]).body["run"]["run_id"]
    )
    RunLifecycleService(urls[2]).start(account_id, run)
    research, _ = research_setup(urls, tmp_path)
    caps, _ = compose_research(research, html_available=True)

    class RecordingModel(Model):
        def __init__(self):
            self.prompts = []

        async def generate(self, **kwargs):
            self.prompts.append(kwargs["messages"][0]["content"])
            return await super().generate(**kwargs)

    model = RecordingModel()

    class LostReply(HtmlActivities):
        lost = False

        @activity.defn(name="produce_html_activity")
        async def produce(self, request: CapabilityExecutionInput) -> str:
            result = await super().produce(request)
            if not self.lost and not model.fail:
                self.lost = True
                raise RuntimeError("lost reply after committing local result")
            return result

    html = LostReply(caps, WebArtifactGenerator(model), research.markdown_publisher.publisher)
    segments = SegmentActivities(caps.store)
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_TEST_NAMESPACE"]
    )
    version = 1

    async def invoke(name, arguments, *, rejected=False):
        nonlocal version
        suffix = str(uuid4())
        decision, operation = f"decision:{suffix}", f"call:{suffix}"
        transcript = f"chain:{run}"
        with execution(urls, account_id, run) as (store, segment, _):
            if version == 1:
                store.begin_operation(f"context:{run}", str(run), "context")
                store.create_transcript(
                    transcript_id=transcript,
                    run_id=str(run),
                    account_id=str(account_id),
                    execution_id=segment.execution_id,
                    conversation_id=None,
                    messages=[{"role": "user", "content": "Compose report"}],
                    operation_id=f"context:{run}",
                )
            store.begin_operation(decision, str(run), "model")
            with UnitOfWork(urls[2]) as uow:
                complete_operation(
                    uow, decision, decision, {"tool_call_arguments": {suffix: arguments}}
                )
            request = ToolExecutionInput(
                AGENT_SCHEMA_VERSION,
                str(run),
                str(account_id),
                RunSource("work", str(wid)),
                RunContext(),
                "react",
                transcript,
                version,
                0,
                operation,
                0,
                CompactToolCall(suffix, name, f"{decision}#{suffix}"),
                execution_id=segment.execution_id,
            )
        observed = await asyncio.wait_for(
            client.execute_workflow(
                ToolExecutionWorkflow.run,
                request,
                id=f"chain-{suffix}",
                task_queue=AGENT_TASK_QUEUE,
                result_type=ToolExecutionResult,
            ),
            90,
        )
        version = observed.transcript_version
        if rejected:
            assert observed.tool_success is False
            with UnitOfWork(urls[2]) as uow:
                assert uow.execute("SELECT count(*) n FROM capability_invocations WHERE invocation_id=%s", (operation,)).fetchone()["n"] == 0
            return observed
        with execution(urls, account_id, run):
            with UnitOfWork(urls[2]) as uow:
                result = caps.facts.read_result(uow, operation)
        assert observed.tool_success is (result["outcome"] == "succeeded")
        return result

    research_names = (
        "prepare_research_activity",
        "create_research_plan_activity",
        "discover_research_sources_activity",
        "rank_research_sources_activity",
        "fetch_research_sources_activity",
        "normalize_research_sources_activity",
        "extract_research_evidence_activity",
        "assess_research_corroboration_activity",
        "analyze_research_gaps_activity",
        "synthesize_research_report_activity",
        "verify_research_citations_activity",
        "compare_previous_research_activity",
        "commit_research_result_activity",
    )
    async with (
        Worker(
            client,
            task_queue=AGENT_TASK_QUEUE,
            workflows=[
                ToolExecutionWorkflow,
                CapabilityInvocationWorkflow,
                ResearchCapabilityWorkflow,
                HtmlCapabilityWorkflow,
            ],
            activities=[
                caps.prepare,
                caps.observe,
                caps.close,
                html.produce,
                segments.acquire,
                segments.release,
                segments.begin_wait,
                segments.finish_wait,
            ],
        ),
        Worker(
            client,
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
            activities=[getattr(research, name) for name in research_names],
        ),
    ):
        report = await invoke("capability__research__v1", {"objective": "Find evidence"})
        markdown = report["outputs"][0]
        before = model.calls
        await invoke("capability__html__v1", {"source_ref": markdown["ref_id"]}, rejected=True)
        assert model.calls == before
        model.fail = True
        failed = await invoke("capability__html__v1", {"source_ref": markdown["ref_id"], "instruction": "Make a complete compact page from the source"})
        assert failed["outcome"] == "insufficient"
        model.fail = False
        first = await invoke("capability__html__v1", {
            "source_ref": markdown["ref_id"], "text": "", "previous_ref": None, "instruction": "Make a complete compact page from the source",
        })
        final = await invoke(
            "capability__html__v1",
            {
                "source_ref": markdown["ref_id"],
                "previous_ref": first["outputs"][0]["ref_id"],
                "instruction": "User explicitly requests a compact layout",
            },
        )
    subject = final["outputs"][0]
    assert subject["object_id"] == first["outputs"][0]["object_id"]
    assert subject["version"] != first["outputs"][0]["version"]
    assert model.calls == 3  # The lost reply reuses the committed successful HTML result.
    assert research.synthesis.calls == 1
    assert all("# Research" in prompt for prompt in model.prompts)
    assert "User explicitly requests a compact layout" in model.prompts[-1]
    assert "result 2" in model.prompts[-1]  # Exact first HTML, rather than a fresh report.
    assert html.lost
    service = ResultAdoptionService(urls[2], caps.facts)
    with execution(urls, account_id, run):
        saved = service.save(
            subject["ref_id"],
            operation_id="save-final",
            directory_id=str(directory),
            name="report.html",
        )
        assert (
            service.save(
                subject["ref_id"],
                operation_id="save-final",
                directory_id=str(directory),
                name="report.html",
            )
            == saved
        )
        evidence = [
            service.evidence(
                "html", AcceptancePredicate("check_passed", subject["ref_id"], "html.basic", "1")
            ),
            service.evidence(
                "save",
                AcceptancePredicate(
                    "workspace_saved",
                    subject["ref_id"],
                    target_id=saved["node_id"],
                    operation_id="save-final",
                ),
            ),
        ]
        with UnitOfWork(urls[2]) as uow:
            row = dict(uow.execute("SELECT * FROM runs WHERE run_id=%s", (run,)).fetchone())
            current = WorkRepository.get(uow, account_id, wid)
            result, ready = generic_result(
                uow,
                row,
                current,
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "deliverable_ready",
                        "evidence": evidence,
                    }
                ),
                "chain-result",
            )
            assert ready
            RunLifecycleService(urls[2]).finish_in_uow(
                uow, row, "succeeded", result=result, accept_result=True
            )
    assert commands.get(account_id, wid)["work"]["status"] == "completed"
    projected = commands.get(account_id, wid)["work"]["workspace_saves"]
    assert any(s["operation_id"] == "save-final" and s["entry_id"] == saved["node_id"]
               and s["state"] == "succeeded" for s in projected)
    from web_artifacts.build import ArtifactBuildService
    from web_artifacts.services import ArtifactService
    from workspace.discovery import WorkspaceDiscovery

    discovery = WorkspaceDiscovery(urls[1])
    trace = discovery.trace(account_id, UUID(saved["node_id"]))
    assert trace["artifact"]["artifact_version_id"] == subject["version"]
    assert trace["source_work_id"] == str(wid)
    artifacts = ArtifactService(urls[1])
    old = artifacts.get_version(account_id, UUID(subject["version"]))["version"]
    # Normal Artifact command revises the completed Work's standalone output.
    # It creates a finite chat Run, reuses the source and leaves Work closed.
    revision = artifacts.create_version(account_id, UUID(subject["object_id"]), str(uuid4()),
                                        "Keep the research and citations; change only the layout")
    pending = revision.body["version"]
    following = owner.execute(
        "SELECT run_id FROM runs WHERE account_id=%s AND executor_key='artifact_html' AND status='queued'",
        (account_id,),
    ).fetchone()["run_id"]
    RunLifecycleService(urls[2]).start(account_id, following)
    build = ArtifactBuildService(urls[2], html.generator, caps, html.publisher)
    await build.execute(UUID(pending["artifact_version_id"]), run_id=following)
    updated = artifacts.get_version(account_id, UUID(pending["artifact_version_id"]))["version"]
    assert updated["status"] == "completed" and updated["parent_version_id"] == subject["version"]
    assert artifacts.get_version(account_id, UUID(subject["version"]))["version"] == old
    assert research.synthesis.calls == 1
    assert model.calls == 4 and "# Research" in model.prompts[-1]
    # Save the new immutable output through the same user-facing Workspace command.
    new_file = owner.execute("SELECT file_id FROM artifact_versions WHERE artifact_version_id=%s",
                             (UUID(updated["artifact_version_id"]),)).fetchone()["file_id"]
    newer_node = WorkspaceCatalog(urls[1]).save_file(account_id, directory, new_file,
                                                   "report-v2.html", "user-save-v2")
    assert discovery.trace(account_id, newer_node)["artifact"]["artifact_version_id"] == updated["artifact_version_id"]
    assert discovery.trace(account_id, UUID(saved["node_id"]))["artifact"]["artifact_version_id"] == subject["version"]
    assert commands.get(account_id, wid)["work"]["status"] == "completed"
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_observations WHERE run_id=%s", (run,)
        ).fetchone()["n"]
        == 4
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM messages WHERE produced_by_run_id=%s", (run,)
        ).fetchone()["n"]
        == 0
    )
