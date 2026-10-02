from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from support.work_fixtures import accept_research, advance_work
from uuid6 import uuid7

from orchestration.research_workflow import ResearchIterationInput, ResearchWorkflowInput
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from research_domain.models import (
    ClaimDraft,
    ResearchReport,
    SourceCandidate,
    SourceContent,
    content_sha256,
)
from research_domain.persistence import ResearchRepository
from research_domain.services import ResearchQueryService
from resources.run_budget import RunBudgetExhausted, RunBudgetService
from web_artifacts.services import ArtifactService
from web_domain.errors import ResourceNotFound
from work_domain.commands import WorkCommandService
from work_domain.models import Requirement

pytestmark = pytest.mark.postgres


def test_trigger_task_is_atomic_idempotent_and_links_research_run(database_url, account_id):
    service = WorkCommandService(database_url, budget_mode="enforce")
    created = accept_research(service,
        account_id,
        str(uuid7()),
        "Research",
        "Find current primary sources",
    )
    key = str(uuid7())
    work_id = UUID(created.body["work"]["work_id"])
    first = advance_work(service, account_id, work_id, key)
    replay = service.advance(account_id, work_id, key, created.body['work']['row_version'])
    assert replay.replayed is True
    assert replay.body == first.body
    with UnitOfWork(database_url) as uow:
        run = uow.execute(
            "SELECT work_id,source_kind,conversation_id,workflow_id,agent_strategy "
            "FROM runs WHERE run_id=%s",
            (first.body["run"]["run_id"],),
        ).fetchone()
        event = uow.execute(
            "SELECT event_type,business_key FROM outbox_events WHERE run_id=%s",
            (first.body["run"]["run_id"],),
        ).fetchone()
        budget = uow.execute(
            "SELECT limits FROM run_budgets WHERE run_id=%s", (first.body["run"]["run_id"],)
        ).fetchone()
    assert str(run["work_id"]) == created.body["work"]["work_id"]
    assert run["source_kind"] == "work"
    assert run["conversation_id"] is None
    assert run["workflow_id"] == f"hpagent-research-{first.body['run']['run_id']}"
    assert run["agent_strategy"] is None
    assert event["event_type"] == "start_research_run"
    assert event["business_key"] == f"start-research-run:{first.body['run']['run_id']}"
    assert budget["limits"]["source_fetches"] == 20


def test_source_authority_and_fetch_priority_are_provider_independent(
    database_url, worker_database_url, account_id
):
    commands = WorkCommandService(database_url)
    task = accept_research(commands, account_id, str(uuid7()), "Ranking", "Rank sources")
    triggered = advance_work(commands, account_id, UUID(task.body["work"]["work_id"]), str(uuid7()))
    run_id = UUID(triggered.body["run"]["run_id"])
    repository = ResearchRepository()
    candidates = [
        SourceCandidate(
            "https://github.com/random/project", provider="githubkit",
            source_type="github_repository",
        ),
        SourceCandidate(
            "https://news.example/item", provider="feedparser", source_type="rss_entry"
        ),
        SourceCandidate(
            "https://official.example/release", provider="searxng",
            metadata={"preferred_domain": True},
        ),
    ]
    with UnitOfWork(worker_database_url) as uow:
        repository.add_candidates(uow, run_id, candidates, lambda uri: uri)

    activities = ResearchActivities(
        worker_database_url, object(), object(), object(), object()
    )
    activities._rank(run_id, 1)
    with UnitOfWork(worker_database_url) as uow:
        rows = uow.execute(
            "SELECT canonical_uri,source_tier FROM source_records WHERE run_id=%s ",
            (run_id,),
        ).fetchall()
        selected = repository.fetch_candidates(uow, run_id, 2, iteration=1)

    tiers = {row["canonical_uri"]: row["source_tier"] for row in rows}
    assert tiers["https://github.com/random/project"] == 2
    assert tiers["https://news.example/item"] == 2
    assert tiers["https://official.example/release"] == 0
    assert selected[0]["canonical_uri"] == "https://official.example/release"
    assert len(selected) == 2


def test_daily_timing_is_an_immutable_requirement_revision(database_url, account_id):
    commands = WorkCommandService(database_url)
    created = accept_research(commands, account_id, str(uuid7()), "Daily", "Track changes")
    work = created.body["work"]
    value = {k:v for k,v in work["requirement"].items() if k in Requirement.__dataclass_fields__}
    value["timing"] = {"schema_version":1,"kind":"daily","timezone":"Asia/Shanghai","local_time":"08:30"}
    key = str(uuid7())
    first = commands.revise(account_id, UUID(work["work_id"]), key, work["row_version"], Requirement.from_dict(value))
    replay = commands.revise(account_id, UUID(work["work_id"]), key, work["row_version"], Requirement.from_dict(value))
    assert replay.replayed and first.body == replay.body
    assert first.body["work"]["current_requirement_revision"] == 2
    value["timing"]["local_time"] = "25:00"
    with pytest.raises(ValueError, match="HH:MM"):
        Requirement.from_dict(value)


@pytest.mark.parametrize(
    "dimension",
    ["sources_discovered", "source_fetches", "research_iterations", "model_calls", "wall_time_ms"],
)
def test_research_budget_dimensions_fail_closed(
    dimension, database_url, worker_database_url, account_id, db
):
    commands = WorkCommandService(database_url, budget_mode="enforce")
    task = accept_research(commands, account_id, str(uuid7()), "Budget", "Exhaust dimension")
    run = advance_work(commands, account_id, UUID(task.body["work"]["work_id"]), str(uuid7()))
    run_id = UUID(run.body["run"]["run_id"])
    db.execute(
        "UPDATE hpagent.run_budgets SET limits=jsonb_set(limits,%s,'0'::jsonb) WHERE run_id=%s",
        ([dimension], run_id),
    )
    with pytest.raises(RunBudgetExhausted, match=dimension):
        RunBudgetService(worker_database_url).reserve(
            run_id, f"acceptance:{dimension}", {dimension: 1}
        )
    row = db.execute(
        "SELECT status FROM hpagent.run_budgets WHERE run_id=%s", (run_id,)
    ).fetchone()
    assert row[0] == "exhausted"


def test_claim_rejects_unknown_cross_run_and_cross_tenant_evidence(
    database_url, worker_database_url, account_id, db
):
    commands = WorkCommandService(database_url)
    task = accept_research(commands, account_id, str(uuid7()), "A", "Source owner")
    source_run = advance_work(commands,
        account_id, UUID(task.body["work"]["work_id"]), str(uuid7())
    )
    source_run_id = UUID(source_run.body["run"]["run_id"])
    source_id, evidence_id = uuid7(), uuid7()
    content_ref = f"research-content:{source_id}"
    db.execute(
        "INSERT INTO hpagent.source_records(source_id,run_id,work_id,account_id,requirement_revision,provider,source_type,"
        "canonical_uri,fetch_status,content_ref,content_hash) "
        "VALUES (%s,%s,%s,%s,1,'test','web','https://example.com/a','fetched',%s,%s)",
        (source_id, source_run_id, task.body["work"]["work_id"], account_id, content_ref, "a" * 64),
    )
    db.execute(
        "INSERT INTO hpagent.source_contents(content_ref,source_id,content_text,byte_size) "
        "VALUES (%s,%s,'source text',11)", (content_ref, source_id),
    )
    db.execute(
        "INSERT INTO hpagent.evidence_items(evidence_id,run_id,source_id,excerpt,content_ref,"
        "source_locator,source_quality) VALUES (%s,%s,%s,'source text',%s,%s::jsonb,1)",
        (evidence_id, source_run_id, source_id, content_ref,
         '{"canonical_uri":"https://example.com/a","content_ref":"x"}'),
    )

    other_account = uuid4()
    db.execute("INSERT INTO hpagent.accounts(account_id) VALUES (%s)", (other_account,))
    other_commands = WorkCommandService(database_url)
    other_task = accept_research(other_commands,
        other_account, str(uuid7()), "B", "Cannot cite A"
    )
    target = advance_work(other_commands,
        other_account, UUID(other_task.body["work"]["work_id"]), str(uuid7())
    )
    report = ResearchReport(
        "Invalid", "# Invalid", (ClaimDraft("Cross tenant", (str(evidence_id),)),)
    )
    with pytest.raises(ValueError, match="outside the Research Run"):
        with UnitOfWork(worker_database_url) as uow:
            ResearchRepository().save_report(uow, UUID(target.body["run"]["run_id"]), report)

    unknown = ResearchReport(
        "Unknown", "# Unknown", (ClaimDraft("Unknown", (str(uuid7()),)),)
    )
    with pytest.raises(ValueError, match="outside the Research Run"):
        with UnitOfWork(worker_database_url) as uow:
            ResearchRepository().save_report(uow, UUID(target.body["run"]["run_id"]), unknown)


@pytest.mark.asyncio
async def test_research_activities_persist_deduplicated_source_and_evidence(
    database_url, worker_database_url, account_id, db
):
    commands = WorkCommandService(database_url, budget_mode="enforce")
    task = accept_research(commands, account_id, str(uuid7()), "Research", "Compare primary sources")
    triggered = advance_work(commands, account_id, UUID(task.body["work"]["work_id"]), str(uuid7()))
    run_id = triggered.body["run"]["run_id"]

    class Discovery:
        async def discover(self, query, *, strategy, limit):
            assert query == "Compare primary sources"
            return [
                SourceCandidate("https://example.com/a?utm_source=x", provider="searxng"),
                SourceCandidate("https://example.org/b", provider="searxng"),
            ][:limit]

    class Canonicalizer:
        def canonicalize(self, uri):
            return uri.replace("?utm_source=x", "")

    class Content:
        async def fetch(self, candidate):
            text = "same normalized source body"
            return SourceContent(
                canonical_uri=candidate.uri,
                title="Source",
                text=text,
                content_hash=content_sha256(text),
                mime_type="text/html",
            )

    class Synthesis:
        async def synthesize(self, objective, evidence):
            return ResearchReport(
                "Report",
                "# Report\n\nSupported statement [E1].",
                (
                    ClaimDraft(
                        "Supported statement",
                        (str(evidence[0]["evidence_id"]),),
                        importance="important",
                    ),
                ),
            )

    activities = ResearchActivities(
        worker_database_url,
        Discovery(),
        Content(),
        Canonicalizer(),
        Synthesis(),
        max_sources=30,
        max_fetches=20,
    )
    request = ResearchWorkflowInput(1, run_id)
    iteration = ResearchIterationInput(1, run_id, 1)
    await activities.prepare_research_activity(request)
    await activities.create_research_plan_activity(request)
    discovered = await activities.discover_research_sources_activity(iteration)
    await activities.rank_research_sources_activity(iteration)
    fetched = await activities.fetch_research_sources_activity(iteration)
    await activities.normalize_research_sources_activity(iteration)
    evidence = await activities.extract_research_evidence_activity(iteration)
    await activities.assess_research_corroboration_activity(iteration)
    await activities.analyze_research_gaps_activity(iteration)
    await activities.synthesize_research_report_activity(request)
    await activities.verify_research_citations_activity(request)
    await activities.compare_previous_research_activity(request)
    await activities.publish_research_artifact_activity(request)
    await activities.complete_research_activity(request)
    assert discovered["item_count"] == 2
    assert fetched["item_count"] == 2
    assert evidence["item_count"] == 1

    with UnitOfWork(database_url) as uow:
        statuses = uow.execute(
            "SELECT fetch_status,count(*) AS count FROM source_records WHERE run_id=%s "
            "GROUP BY fetch_status ORDER BY fetch_status",
            (run_id,),
        ).fetchall()
        evidence_count = uow.execute(
            "SELECT count(*) AS count FROM evidence_items WHERE run_id=%s", (run_id,)
        ).fetchone()["count"]
        run = uow.execute("SELECT status FROM runs WHERE run_id=%s", (run_id,)).fetchone()
        trace = uow.execute(
            "SELECT status,strategy FROM trace_runs WHERE run_id=%s", (run_id,)
        ).fetchone()
        budget = uow.execute("SELECT used FROM run_budgets WHERE run_id=%s", (run_id,)).fetchone()[
            "used"
        ]
        report = uow.execute(
            "SELECT citation_status,artifact_id FROM research_reports WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        citation = uow.execute(
            "SELECT verification_status FROM research_citations WHERE run_id=%s",
            (run_id,),
        ).fetchone()
        locator = uow.execute(
            "SELECT source_locator FROM evidence_items WHERE run_id=%s", (run_id,)
        ).fetchone()["source_locator"]
    assert {row["fetch_status"]: row["count"] for row in statuses} == {
        "duplicate": 1,
        "fetched": 1,
    }
    assert evidence_count == 1
    assert run["status"] == "succeeded"
    assert trace == {"status": "completed", "strategy": "research"}
    assert budget["sources_discovered"] == 2
    assert budget["source_fetches"] == 2
    assert budget["research_iterations"] == 1
    assert report["citation_status"] == "needs_review"
    assert citation["verification_status"] == "weak_source"
    assert locator["canonical_uri"] == "https://example.com/a"
    assert locator["content_ref"].startswith("research-content:")
    assert locator["text_span"] == {"start": 0, "end": 27}
    assert locator["excerpt"] == "same normalized source body"
    assert report["artifact_id"] is not None
    run_view = ResearchQueryService(database_url).report(account_id, UUID(run_id))["report"]
    assert run_view["artifact_id"] == str(report["artifact_id"])
    artifact_view = ArtifactService(database_url).get_artifact(account_id, report["artifact_id"])
    assert artifact_view["artifact"]["conversation_id"] is None
    assert artifact_view["artifact"]["research_run_id"] == run_id
    # Terminal publication replay cannot change the persisted Artifact identity.
    activities._publish(UUID(run_id))
    with UnitOfWork(database_url) as uow:
        published = uow.execute(
            "SELECT count(DISTINCT a.artifact_id) AS artifacts,"
            "count(DISTINCT av.artifact_version_id) AS versions FROM artifacts a "
            "JOIN artifact_versions av ON av.artifact_id=a.artifact_id "
            "WHERE a.research_run_id=%s", (run_id,),
        ).fetchone()
    assert published == {"artifacts": 1, "versions": 1}

    db.execute(
        "UPDATE hpagent.research_citations SET locator='{}'::jsonb WHERE run_id=%s",
        (run_id,),
    )
    with UnitOfWork(worker_database_url) as uow:
        ResearchRepository().verify_citations(uow, UUID(run_id))
    invalid = db.execute(
        "SELECT verification_status FROM hpagent.research_citations WHERE run_id=%s",
        (run_id,),
    ).fetchone()[0]
    assert invalid == "invalid"

    other_account = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other_account,))
    with pytest.raises(ResourceNotFound):
        commands.get(other_account, UUID(task.body["work"]["work_id"]))
    with pytest.raises(ResourceNotFound):
        ResearchQueryService(database_url).evidence(other_account, UUID(run_id))
    with pytest.raises(ResourceNotFound):
        ResearchQueryService(database_url).report(other_account, UUID(run_id))
    with pytest.raises(ResourceNotFound):
        ArtifactService(database_url).get_artifact(other_account, report["artifact_id"])

    # A later successful Run owns a different Artifact. With no authorized
    # Workspace history entry, the baseline stays empty despite the same Task.
    second = advance_work(commands, account_id, UUID(task.body["work"]["work_id"]), str(uuid7()))
    second_request = ResearchWorkflowInput(1, second.body["run"]["run_id"])
    second_iteration = ResearchIterationInput(1, second.body["run"]["run_id"], 1)
    for call, argument in (
        (activities.prepare_research_activity, second_request),
        (activities.create_research_plan_activity, second_request),
        (activities.discover_research_sources_activity, second_iteration),
        (activities.rank_research_sources_activity, second_iteration),
        (activities.fetch_research_sources_activity, second_iteration),
        (activities.normalize_research_sources_activity, second_iteration),
        (activities.extract_research_evidence_activity, second_iteration),
        (activities.assess_research_corroboration_activity, second_iteration),
        (activities.analyze_research_gaps_activity, second_iteration),
        (activities.synthesize_research_report_activity, second_request),
        (activities.verify_research_citations_activity, second_request),
        (activities.compare_previous_research_activity, second_request),
        (activities.publish_research_artifact_activity, second_request),
        (activities.complete_research_activity, second_request),
    ):
        await call(argument)
    with UnitOfWork(database_url) as uow:
        second_report = uow.execute(
            "SELECT previous_run_id,daily_diff,artifact_id FROM research_reports WHERE run_id=%s",
            (second.body["run"]["run_id"],),
        ).fetchone()
        artifact_count = uow.execute(
            "SELECT count(*) AS count FROM artifacts WHERE research_run_id IN (%s,%s)",
            (run_id, second.body["run"]["run_id"]),
        ).fetchone()["count"]
    assert second_report["previous_run_id"] is None
    assert len(second_report["daily_diff"]["continuing"]) == 0
    assert artifact_count == 2


@pytest.mark.asyncio
async def test_fetch_activity_retry_reuses_original_budget_reservation(
    database_url, worker_database_url, account_id
):
    commands = WorkCommandService(database_url, budget_mode="enforce")
    task = accept_research(commands, account_id, str(uuid7()), "Retry", "Fetch retry")
    triggered = advance_work(commands, account_id, UUID(task.body["work"]["work_id"]), str(uuid7()))
    run_id = UUID(triggered.body["run"]["run_id"])

    class Discovery:
        async def discover(self, query, *, strategy, limit):
            return [
                SourceCandidate("https://example.com/one", provider="searxng"),
                SourceCandidate("https://example.com/two", provider="searxng"),
            ][:limit]

    class Canonicalizer:
        def canonicalize(self, uri):
            return uri

    class Content:
        async def fetch(self, candidate):
            text = f"content for {candidate.uri}"
            return SourceContent(
                canonical_uri=candidate.uri,
                title="Source",
                text=text,
                content_hash=content_sha256(text),
            )

    activities = ResearchActivities(
        worker_database_url, Discovery(), Content(), Canonicalizer(), object()
    )
    request = ResearchWorkflowInput(1, str(run_id))
    iteration = ResearchIterationInput(1, str(run_id), 1)
    await activities.prepare_research_activity(request)
    await activities.create_research_plan_activity(request)
    await activities.discover_research_sources_activity(iteration)
    await activities.rank_research_sources_activity(iteration)

    operation_id = f"research:{run_id}:Fetch:1:budget:v1"
    RunBudgetService(worker_database_url).reserve(
        run_id, operation_id, {"source_fetches": 2}
    )
    with UnitOfWork(worker_database_url) as uow:
        first_source = uow.execute(
            "SELECT source_id FROM source_records WHERE run_id=%s ORDER BY source_id LIMIT 1",
            (run_id,),
        ).fetchone()["source_id"]
        uow.execute(
            "UPDATE source_records SET fetch_status='failed' WHERE source_id=%s",
            (first_source,),
        )

    result = await activities.fetch_research_sources_activity(iteration)
    assert result["item_count"] == 2
    with UnitOfWork(worker_database_url) as uow:
        ledger = uow.execute(
            "SELECT state,reserved_amount,actual_amount FROM run_usage_ledger "
            "WHERE run_id=%s AND operation_id=%s AND dimension='source_fetches'",
            (run_id, operation_id),
        ).fetchone()
    assert ledger == {"state": "settled", "reserved_amount": 2, "actual_amount": 2}
