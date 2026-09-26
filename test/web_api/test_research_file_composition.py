from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchIterationInput, ResearchWorkflowInput
from research_activities import ResearchActivities
from research_domain.models import (
    ClaimDraft,
    ResearchReport,
    SourceCandidate,
    SourceContent,
    content_sha256,
)
from storage.tenant_file_store import TenantFileStore


def _headers(csrf: str, key: str | None = None) -> dict[str, str]:
    result = {"Origin": "https://testserver", "X-CSRF-Token": csrf}
    if key:
        result["Idempotency-Key"] = key
    return result


@pytest.mark.asyncio
async def test_standalone_research_report_becomes_downloadable_run_output(
    tmp_path, seed_identity, client_factory, worker_database_url, db,
):
    seed_identity("research-composition-user")
    store_root = tmp_path / "store"
    client = client_factory(file_upload_enabled=True, file_store_root=str(store_root))
    login = client.post(
        "/auth/login",
        json={"username": "research-composition-user", "password": "correct-password"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    csrf = client.get("/api/v1/me").json()["csrf_token"]
    objective = "Research recent Agent Memory technical changes."
    created = client.post(
        "/api/v1/tasks",
        json={
            "title": "Agent Memory", "objective": objective,
        },
        headers=_headers(csrf, str(uuid4())),
    )
    assert created.status_code == 201
    task_id = created.json()["task"]["task_id"]
    triggered = client.post(
        f"/api/v1/tasks/{task_id}/runs", json={},
        headers=_headers(csrf, str(uuid4())),
    )
    assert triggered.status_code == 202
    run_id = triggered.json()["run"]["run_id"]

    class Discovery:
        async def discover(self, query, *, strategy, limit):
            return [
                SourceCandidate("https://example.com/memory", provider="fixture"),
                SourceCandidate("https://example.org/memory", provider="fixture"),
            ][:limit]

    class Canonicalizer:
        def canonicalize(self, uri):
            return uri

    class Content:
        async def fetch(self, candidate):
            text = f"Agent Memory evidence from {candidate.uri}"
            return SourceContent(
                canonical_uri=candidate.uri, title="Memory source", text=text,
                content_hash=content_sha256(text), mime_type="text/html",
            )

    markdown = "# Agent Memory\n\nRecent systems use durable memory evidence [E1].\n"

    class Synthesis:
        async def synthesize(self, objective, evidence):
            return ResearchReport(
                "Agent Memory", markdown,
                (ClaimDraft("Durable memory evidence", (str(evidence[0]["evidence_id"]),)),),
            )

    publisher = ResearchMarkdownPublisher(OutputPublisher(
        worker_database_url, TenantFileStore(store_root, max_bytes=1024 * 1024)
    ))
    activities = ResearchActivities(
        worker_database_url, Discovery(), Content(), Canonicalizer(), Synthesis(),
        min_evidence=1, min_distinct_sources=1, markdown_publisher=publisher,
    )
    request = ResearchWorkflowInput(1, run_id)
    iteration = ResearchIterationInput(1, run_id, 1)
    await activities.prepare_research_activity(request)
    await activities.create_research_plan_activity(request)
    await activities.discover_research_sources_activity(iteration)
    await activities.rank_research_sources_activity(iteration)
    await activities.fetch_research_sources_activity(iteration)
    await activities.normalize_research_sources_activity(iteration)
    await activities.extract_research_evidence_activity(iteration)
    await activities.assess_research_corroboration_activity(iteration)
    await activities.analyze_research_gaps_activity(iteration)
    await activities.synthesize_research_report_activity(request)
    await activities.verify_research_citations_activity(request)
    await activities.compare_previous_research_activity(request)
    await activities.publish_research_artifact_activity(request)
    await activities.publish_research_artifact_activity(request)
    await activities.complete_research_activity(request)

    run = client.get(f"/api/v1/tasks/{task_id}/runs/{run_id}").json()["run"]
    evidence = client.get(
        f"/api/v1/tasks/{task_id}/runs/{run_id}/evidence"
    ).json()["evidence"]
    report = client.get(
        f"/api/v1/tasks/{task_id}/runs/{run_id}/report"
    ).json()["report"]
    assert run["status"] == "completed"
    assert evidence and report["artifact_id"]
    assert report["report_markdown"].startswith(markdown)
    attachment = run["published_file"]
    assert attachment is not None
    history_tasks = client.get("/api/v1/tasks").json()["items"]
    assert any(item["task_id"] == task_id for item in history_tasks)
    history_runs = client.get(f"/api/v1/tasks/{task_id}/runs").json()["items"]
    assert history_runs[0]["published_file"] == attachment
    assert attachment["file_name"] == "research-report.md"
    downloaded = client.get(attachment["download_url"])
    assert downloaded.status_code == 200
    assert downloaded.content.decode("utf-8") == report["report_markdown"]
    chat = client.post(
        "/api/v1/conversations", json={"title": "Read Task output"},
        headers=_headers(csrf, str(uuid4())),
    ).json()["conversation"]
    candidates = client.get(
        f"/api/v1/conversations/{chat['conversation_id']}/file-candidates"
    ).json()["items"]
    # A new Conversation has no Workspace grant and cannot discover Task output.
    assert all(item["file_id"] != attachment["file_id"] for item in candidates)
    client.cookies.clear()
    assert client.get(attachment["download_url"]).status_code == 401
    rows = db.execute(
        "SELECT count(*),min(sf.encoding) FROM hpagent.run_files rf "
        "JOIN hpagent.stored_files sf USING(file_id) "
        "WHERE rf.run_id=%s AND rf.direction='output'",
        (UUID(run_id),),
    ).fetchone()
    assert rows[0] == 1
    assert rows[1] == "utf-8"
    assert db.execute(
        "SELECT sf.conversation_id,sf.source_run_id FROM stored_files sf "
        "WHERE sf.file_id=%s", (UUID(attachment["file_id"]),),
    ).fetchone() == (None, UUID(run_id))
