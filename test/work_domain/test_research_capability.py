"""Research local results share the S2 fence/budget/immutable-ref boundary."""

from uuid import UUID, uuid4

import pytest
from test_capability_contracts import scope
from test_capability_facts import chat, execution

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from agent_activities.store import AgentDataStore
from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchIterationInput, ResearchWorkflowInput
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from research_domain.capability import research_definition
from research_domain.models import (
    ClaimDraft,
    ResearchReport,
    SourceCandidate,
    SourceContent,
    content_sha256,
)
from research_domain.services import ResearchQueryService
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityRegistry
from storage.tenant_file_store import TenantFileStore
from web_domain.errors import ResourceNotFound

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class Discovery:
    async def discover(self, query, *, strategy, limit):
        return [SourceCandidate("https://source.example/facts", title="Facts")][:limit]


class Content:
    async def fetch(self, candidate):
        text = "Primary evidence for the requested research objective. " * 20
        return SourceContent(candidate.uri, "Facts", text, content_sha256(text))


class Canonicalizer:
    def canonicalize(self, uri):
        return uri


class Synthesis:
    calls = 0

    async def synthesize(self, objective, evidence):
        self.calls += 1
        return ResearchReport(
            "Research",
            "# Research\n\nA finding.",
            (ClaimDraft("A finding", (str(evidence[0]["evidence_id"]),)),),
        )


def setup(urls, tmp_path):
    caps = CapabilityActivities(
        AgentDataStore(urls[2]),
        CapabilityFacts(
            CapabilityRegistry((research_definition(),)), services=frozenset({"research"})
        ),
        (CapabilityBinding("research", 1, "ResearchCapabilityWorkflow"),),
    )
    research = ResearchActivities(
        urls[2],
        Discovery(),
        Content(),
        Canonicalizer(),
        Synthesis(),
        min_evidence=1,
        min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(
            OutputPublisher(urls[2], TenantFileStore(tmp_path / "files", max_bytes=1024 * 1024))
        ),
    )
    research.capabilities = caps
    return research, caps


def admit(caps, urls, account_id, run_id, suffix):
    operation = f"{run_id}:research:{suffix}"
    with execution(urls, account_id, run_id) as (store, _, _):
        store.begin_operation(operation, str(run_id), "tool")
        with UnitOfWork(urls[2]) as uow:
            caps.admit_in_uow(
                uow, *caps.facts._context(uow), caps.facts.registry.get("research", 1), operation
            )
            row = caps.facts.create(
                uow,
                operation_id=operation,
                capability_key="research",
                contract_version=1,
                inputs={"objective": "Find evidence"},
                scope=scope(),
                invocation_limits=research_definition().limits,
            )
    return ResearchWorkflowInput(
        1, str(run_id), operation, str(account_id), str(row["child_execution_id"])
    )


async def produce(research, request, *, before_commit=None, before_verify=None):
    await research.prepare_research_activity(request)
    await research.create_research_plan_activity(request)
    iteration = ResearchIterationInput(
        1, request.run_id, 1, request.invocation_id, request.account_id, request.execution_id
    )
    for name in (
        "discover_research_sources_activity",
        "rank_research_sources_activity",
        "fetch_research_sources_activity",
        "normalize_research_sources_activity",
        "extract_research_evidence_activity",
        "assess_research_corroboration_activity",
        "analyze_research_gaps_activity",
    ):
        await getattr(research, name)(iteration)
    await research.synthesize_research_report_activity(request)
    if before_verify:
        before_verify()
    await research.verify_research_citations_activity(request)
    await research.compare_previous_research_activity(request)
    if before_commit:
        before_commit()
    await research.commit_research_result_activity(request)


@pytest.mark.parametrize("surface", ["chat", "work"])
async def test_same_run_two_research_instances_have_independent_readable_results(
    urls, owner, account_id, tmp_path, surface
):
    if surface == "chat":
        run_id = chat(urls, account_id)
    else:
        from run_domain.lifecycle import RunLifecycleService
        from work_domain.commands import WorkCommandService
        from work_domain.models import Requirement

        commands = WorkCommandService(urls[1])
        work = commands.accept(
            account_id,
            str(uuid4()),
            "Research composition",
            Requirement("Find evidence", "generic_work", {"schema_version": 1}),
        ).body["work"]
        run_id = UUID(
            commands.advance(
                account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"]
            ).body["run"]["run_id"]
        )
        RunLifecycleService(urls[2]).start(account_id, run_id)
    research, caps = setup(urls, tmp_path)
    from agent_workflows.capability_contracts import CapabilityClose, CapabilityExecutionInput

    requests = []
    for index in range(2):
        request = admit(caps, urls, account_id, run_id, str(index))
        await produce(research, request)
        calls = research.synthesis.calls
        await research.commit_research_result_activity(request)
        assert research.synthesis.calls == calls
        child = CapabilityExecutionInput(
            request.account_id,
            request.run_id,
            request.execution_id,
            request.invocation_id,
            "ResearchCapabilityWorkflow",
            3600,
            4,
            request.invocation_id + ":produce",
        )
        await caps.close(CapabilityClose(child, "succeeded"))
        requests.append(request)
    first, second = requests
    reports = [
        ResearchQueryService(urls[1]).report(account_id, run_id, req.invocation_id)["report"]
        for req in (first, second)
    ]
    assert reports[0]["markdown_file_id"] != reports[1]["markdown_file_id"]
    assert all(report["artifact_id"] is None for report in reports)
    for request in (first, second):
        with execution(urls, account_id, run_id):
            with UnitOfWork(urls[2]) as uow:
                result = caps.facts.read_result(uow, request.invocation_id)
                assert result["outcome"] == "succeeded"
                assert result["checks"][0]["verdict"] == "passed"
                assert caps.facts.read_ref(uow, UUID(result["outputs"][0]["ref_id"]))
    assert (
        owner.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()["status"]
        == "running"
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM research_plans WHERE run_id=%s", (run_id,)
        ).fetchone()["n"]
        == 2
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM workspace_save_operations WHERE source_run_id=%s", (run_id,)
        ).fetchone()["n"]
        == 0
    )
    with pytest.raises(ResourceNotFound):
        ResearchQueryService(urls[1]).report(uuid4(), run_id, first.invocation_id)


async def test_invalid_locator_returns_insufficient_and_keeps_markdown(
    urls, owner, account_id, tmp_path
):
    run_id = chat(urls, account_id)
    research, caps = setup(urls, tmp_path)
    request = admit(caps, urls, account_id, run_id, "invalid")

    def invalidate():
        owner.execute(
            "UPDATE research_citations SET locator=jsonb_set(locator,'{canonical_uri}',to_jsonb('https://wrong.example/'::text)) WHERE invocation_id=%s",
            (request.invocation_id,),
        )

    await produce(research, request, before_verify=invalidate)
    with execution(urls, account_id, run_id):
        with UnitOfWork(urls[2]) as uow:
            result = caps.facts.read_result(uow, request.invocation_id)
    assert result["outcome"] == "insufficient"
    assert result["checks"][0]["verdict"] == "failed"
    assert result["outputs"][0]["type"] == "markdown"
    assert result["gaps"][0]["code"] == "invalid_citation"


async def test_fixed_parent_completes_without_reading_view(urls, owner, account_id, tmp_path):
    from support.work_fixtures import accept_research, advance_work

    from research_activities.parent import ResearchParentActivities
    from work_domain.commands import WorkCommandService

    commands = WorkCommandService(urls[1])
    work = accept_research(commands, account_id, str(uuid4()), "Research", "Find evidence")
    run_id = UUID(
        advance_work(commands, account_id, UUID(work.body["work"]["work_id"]), str(uuid4())).body[
            "run"
        ]["run_id"]
    )
    research, caps = setup(urls, tmp_path)
    parent = ResearchParentActivities(research, caps)
    child = await parent.prepare_research_parent_activity(ResearchWorkflowInput(1, str(run_id)))
    request = ResearchWorkflowInput(
        1, str(run_id), child.invocation_id, child.account_id, child.execution_id
    )
    await produce(research, request)
    from agent_workflows.capability_contracts import CapabilityClose

    await caps.close(CapabilityClose(child, "succeeded"))
    await parent.complete_research_activity(
        ResearchWorkflowInput(1, str(run_id), child.invocation_id)
    )
    row = owner.execute(
        "SELECT r.status,rr.artifact_id,rr.markdown_file_id FROM runs r JOIN research_reports rr USING(run_id) WHERE r.run_id=%s",
        (run_id,),
    ).fetchone()
    assert row["status"] == "succeeded" and row["artifact_id"] is None and row["markdown_file_id"]


async def test_no_evidence_keeps_draft_without_synthesis_or_fake_citations(
    urls, owner, account_id, tmp_path
):
    run_id = chat(urls, account_id)
    research, caps = setup(urls, tmp_path)

    class EmptyDiscovery:
        async def discover(self, query, *, strategy, limit):
            return []

    research.discovery = EmptyDiscovery()
    request = admit(caps, urls, account_id, run_id, "empty")
    await produce(research, request)
    assert research.synthesis.calls == 0
    with execution(urls, account_id, run_id):
        with UnitOfWork(urls[2]) as uow:
            result = caps.facts.read_result(uow, request.invocation_id)
    assert result["outcome"] == "insufficient"
    assert result["gaps"][0]["code"] == "invalid_structure"
    report = ResearchQueryService(urls[1]).report(account_id, run_id, request.invocation_id)[
        "report"
    ]
    assert report["markdown_file_id"] and report["report_structured_json"]["claims"] == []


async def test_research_rows_reject_cross_account_and_sibling_instance(
    urls, owner, account_id, tmp_path
):
    import psycopg

    from agent_workflows.capability_contracts import CapabilityClose, CapabilityExecutionInput

    run_id = chat(urls, account_id)
    _, caps = setup(urls, tmp_path)
    first = admit(caps, urls, account_id, run_id, "owner")
    other_account = uuid4()
    owner.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other_account,))
    with pytest.raises(psycopg.errors.ForeignKeyViolation), owner.transaction():
        owner.execute(
            "INSERT INTO research_plans(run_id,invocation_id,account_id,plan) VALUES (%s,%s,%s,'{}')",
            (run_id, first.invocation_id, other_account),
        )
    child = CapabilityExecutionInput(
        first.account_id,
        first.run_id,
        first.execution_id,
        first.invocation_id,
        "ResearchCapabilityWorkflow",
        3600,
        4,
        first.invocation_id + ":produce",
    )
    await caps.close(CapabilityClose(child, "failed"))
    second = admit(caps, urls, account_id, run_id, "sibling")
    source_id, evidence_id = uuid4(), uuid4()
    content_ref = f"research-content:{source_id}"
    owner.execute(
        "INSERT INTO source_records(source_id,run_id,invocation_id,account_id,provider,source_type,canonical_uri) VALUES (%s,%s,%s,%s,'fixture','web','https://source.example/')",
        (source_id, run_id, first.invocation_id, account_id),
    )
    owner.execute(
        "INSERT INTO source_contents(content_ref,source_id,content_text,byte_size) VALUES (%s,%s,'evidence',8)",
        (content_ref, source_id),
    )
    with pytest.raises(psycopg.errors.RaiseException, match="source mismatch"), owner.transaction():
        owner.execute(
            "INSERT INTO evidence_items(evidence_id,run_id,invocation_id,source_id,excerpt,content_ref,source_locator,source_quality) VALUES (%s,%s,%s,%s,'evidence',%s,'{}',1)",
            (evidence_id, run_id, second.invocation_id, source_id, content_ref),
        )


async def test_fixed_parent_keeps_existing_explicit_artifact_acceptance(
    urls, owner, account_id, tmp_path
):
    from support.research_driver import ResearchDriver
    from support.work_fixtures import advance_work

    from research_domain.models import SourceStrategy
    from work_domain.commands import WorkCommandService
    from work_domain.models import Requirement

    commands = WorkCommandService(urls[1])
    work = commands.accept(
        account_id,
        str(uuid4()),
        "Review Research",
        Requirement(
            "Find evidence",
            "research_report",
            {"schema_version": 1, "source_strategy": SourceStrategy().to_dict()},
            acceptance_criteria=(
                {"id": "report", "required": True, "evidence_types": ["research_report"]},
                {"id": "review", "required": True, "evidence_types": ["user_acceptance"]},
            ),
        ),
    ).body["work"]
    run_id = UUID(
        advance_work(commands, account_id, UUID(work["work_id"]), str(uuid4())).body["run"][
            "run_id"
        ]
    )
    driver = ResearchDriver(
        urls[2],
        Discovery(),
        Content(),
        Canonicalizer(),
        Synthesis(),
        min_evidence=1,
        min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(
            OutputPublisher(
                urls[2], TenantFileStore(tmp_path / "review-files", max_bytes=1024 * 1024)
            )
        ),
    )
    await driver.prepare_research_activity(ResearchWorkflowInput(1, str(run_id)))
    request = driver.local(ResearchWorkflowInput(1, str(run_id)))
    await produce(driver.research, request)
    await driver.publish_research_artifact_activity(ResearchWorkflowInput(1, str(run_id)))
    await driver.complete_research_activity(ResearchWorkflowInput(1, str(run_id)))
    current = commands.get(account_id, UUID(work["work_id"]))["work"]
    report = ResearchQueryService(urls[1]).report(account_id, run_id)["report"]
    accepted = commands.integration(
        account_id,
        UUID(work["work_id"]),
        str(uuid4()),
        current["row_version"],
        "accept_result",
        {
            "requirement_revision": current["current_requirement_revision"],
            "artifact_version_id": UUID(report["artifact_version_id"]),
        },
    )
    assert accepted.body["work"]["status"] == "completed"
