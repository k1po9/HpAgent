"""P4 Research continuity and required Workspace save on isolated PostgreSQL."""
from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest

from file_runtime import OutputPublisher, ResearchMarkdownPublisher
from orchestration.research_workflow import ResearchIterationInput, ResearchWorkflowInput
from persistence.uow import UnitOfWork
from research_activities import ResearchActivities
from research_domain.history import authorized_history
from research_domain.models import (
    ClaimDraft,
    ResearchReport,
    SourceCandidate,
    SourceContent,
    content_sha256,
)
from research_domain.services import ResearchTaskCommandService
from storage.tenant_file_store import TenantFileStore
from workspace.catalog import WorkspaceCatalog, WorkspaceConflict
from workspace.resources import ResourceDenied, ResourcePolicy

pytestmark = pytest.mark.postgres


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
    def __init__(self):
        self.objectives = []

    async def synthesize(self, objective, evidence):
        self.objectives.append(objective)
        return ResearchReport("Fixture", "# Fixed report\n", (
            ClaimDraft("Fixed claim", (str(evidence[0]["evidence_id"]),)),
        ))


async def execute(activities, run_id):
    request = ResearchWorkflowInput(1, str(run_id))
    iteration = ResearchIterationInput(1, str(run_id), 1)
    await activities.prepare_research_activity(request)
    await activities.create_research_plan_activity(request)
    for stage in (
        activities.discover_research_sources_activity,
        activities.rank_research_sources_activity,
        activities.fetch_research_sources_activity,
        activities.normalize_research_sources_activity,
        activities.extract_research_evidence_activity,
        activities.assess_research_corroboration_activity,
        activities.analyze_research_gaps_activity,
    ):
        await stage(iteration)
    for stage in (
        activities.synthesize_research_report_activity,
        activities.verify_research_citations_activity,
        activities.compare_previous_research_activity,
        activities.publish_research_artifact_activity,
        activities.save_research_workspace_activity,
        activities.complete_research_activity,
    ):
        await stage(request)


@pytest.mark.asyncio
async def test_thirty_standalone_runs_use_bounded_authorized_history_and_save(
    database_url, worker_database_url, account_id, tmp_path,
):
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    target = catalog.create_directory(account_id, parent, "日报")
    commands = ResearchTaskCommandService(database_url)
    task_id = UUID(commands.create_task(account_id, str(uuid4()), "Daily", "Track facts",
                                        output_directory_id=target,
                                        output_required=True).body["task_id"])
    synthesis = Synthesis()
    publisher = ResearchMarkdownPublisher(OutputPublisher(
        worker_database_url, TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024)))
    activities = ResearchActivities(worker_database_url, Discovery(), Content(),
                                    Canonicalizer(), synthesis, min_evidence=1,
                                    min_distinct_sources=1, markdown_publisher=publisher)
    run_ids = []
    for index in range(30):
        run_id = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
        if index == 1:
            catalog.move(account_id, target, parent, "改名后的日报")
            with pytest.raises(WorkspaceConflict, match="still bound"):
                catalog.remove(account_id, target)
        await execute(activities, run_id)
        run_ids.append(run_id)
    with UnitOfWork(database_url) as uow:
        runs = uow.execute("SELECT count(*) AS n FROM runs WHERE task_id=%s AND status='completed'",
                           (task_id,)).fetchone()["n"]
        reports = uow.execute("SELECT count(*) AS n FROM research_reports rr JOIN runs r "
                              "ON r.run_id=rr.run_id WHERE r.task_id=%s", (task_id,)).fetchone()["n"]
        entries = uow.execute("SELECT count(*) AS n FROM research_run_save_intents "
                              "WHERE account_id=%s AND state='succeeded' AND entry_id IS NOT NULL",
                              (account_id,)).fetchone()["n"]
        second = uow.execute("SELECT selected FROM research_history_baselines WHERE run_id=%s",
                             (run_ids[1],)).fetchone()["selected"]
        largest = uow.execute("SELECT max(length(selected::text)) AS n FROM "
                              "research_history_baselines WHERE account_id=%s",
                              (account_id,)).fetchone()["n"]
    assert (runs, reports, entries) == (30, 30, 30)
    assert second[0]["run_id"] == str(run_ids[0])
    assert "Authorized prior report claims" in synthesis.objectives[1]
    assert largest < 12000
    await asyncio.to_thread(activities._save_workspace, run_ids[-1])
    with UnitOfWork(database_url) as uow:
        assert uow.execute("SELECT count(*) AS n FROM workspace_nodes WHERE account_id=%s "
                           "AND parent_id=%s AND kind='file'", (account_id, target)
                           ).fetchone()["n"] == 30


@pytest.mark.asyncio
async def test_target_change_freezes_old_run_and_save_retry_skips_research(
    database_url, worker_database_url, account_id, tmp_path, monkeypatch,
):
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    original = catalog.create_directory(account_id, parent, "原目标")
    replacement = catalog.create_directory(account_id, parent, "新目标")
    commands = ResearchTaskCommandService(database_url)
    task_id = UUID(commands.create_task(account_id, str(uuid4()), "Daily", "Track facts",
                                        output_directory_id=original,
                                        output_required=True).body["task_id"])
    run_id = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    commands.update_output(account_id, task_id, replacement, True)
    synthesis = Synthesis()
    activities = ResearchActivities(worker_database_url, Discovery(), Content(),
        Canonicalizer(), synthesis, min_evidence=1, min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(OutputPublisher(
            worker_database_url, TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024))))
    request = ResearchWorkflowInput(1, str(run_id))
    iteration = ResearchIterationInput(1, str(run_id), 1)
    await activities.prepare_research_activity(request)
    await activities.create_research_plan_activity(request)
    for stage in (
        activities.discover_research_sources_activity,
        activities.rank_research_sources_activity,
        activities.fetch_research_sources_activity,
        activities.normalize_research_sources_activity,
        activities.extract_research_evidence_activity,
        activities.assess_research_corroboration_activity,
        activities.analyze_research_gaps_activity,
    ):
        await stage(iteration)
    for stage in (activities.synthesize_research_report_activity,
                  activities.verify_research_citations_activity,
                  activities.compare_previous_research_activity,
                  activities.publish_research_artifact_activity):
        await stage(request)
    with pytest.raises(RuntimeError, match="required_workspace_save_incomplete"):
        activities._complete(run_id)
    with UnitOfWork(database_url) as uow:
        uow.execute("UPDATE research_run_save_intents SET state='succeeded' WHERE run_id=%s",
                    (run_id,))
    with pytest.raises(RuntimeError, match="required_workspace_save_incomplete"):
        activities._complete(run_id)
    with UnitOfWork(database_url) as uow:
        uow.execute("UPDATE research_run_save_intents SET state='pending' WHERE run_id=%s",
                    (run_id,))
    original_save = WorkspaceCatalog.save_file
    attempts = 0

    def transient_failure(self, *args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("injected save failure")
        if attempts == 2:
            original_save(self, *args, **kwargs)
            raise RuntimeError("injected post-commit failure")
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(WorkspaceCatalog, "save_file", transient_failure)
    with pytest.raises(RuntimeError, match="injected save failure"):
        activities._save_workspace(run_id)
    with pytest.raises(RuntimeError, match="injected post-commit failure"):
        activities._save_workspace(run_id)
    activities._save_workspace(run_id)
    activities._save_workspace(run_id)
    await activities.complete_research_activity(request)
    with UnitOfWork(database_url) as uow:
        intent = uow.execute("SELECT target_directory_id,entry_id,state FROM "
                             "research_run_save_intents WHERE run_id=%s", (run_id,)).fetchone()
        entry = uow.execute("SELECT parent_id FROM workspace_nodes WHERE node_id=%s",
                            (intent["entry_id"],)).fetchone()
        assert uow.execute("SELECT count(*) AS n FROM workspace_save_operations WHERE "
                           "operation_id=%s", (f"workspace-save:{run_id}:research_markdown",)
                           ).fetchone()["n"] == 1
        source = uow.execute("SELECT source_kind,source_run_id FROM "
                             "workspace_save_operations WHERE operation_id=%s",
                             (f"workspace-save:{run_id}:research_markdown",)).fetchone()
        assert source == {"source_kind": "task_auto", "source_run_id": run_id}
        assert uow.execute("SELECT count(*) AS n FROM run_files WHERE run_id=%s AND "
                           "direction='output'", (run_id,)).fetchone()["n"] == 1
    assert intent["target_directory_id"] == entry["parent_id"] == original
    assert intent["state"] == "succeeded"
    assert attempts == 3 and len(synthesis.objectives) == 1
    detail = commands.get_run(account_id, task_id, run_id)
    assert detail["save_status"] == "succeeded"
    assert detail["save_entry_id"] == str(intent["entry_id"])
    assert commands.list_runs(account_id, task_id)["items"][0]["save_status"] == "succeeded"


@pytest.mark.asyncio
async def test_history_read_revocation_blocks_snapshot_even_after_selection(
    database_url, worker_database_url, account_id, tmp_path,
):
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    target = catalog.create_directory(account_id, parent, "历史")
    commands = ResearchTaskCommandService(database_url)
    task_id = UUID(commands.create_task(account_id, str(uuid4()), "Daily", "Track facts",
                                        output_directory_id=target,
                                        output_required=True).body["task_id"])
    activities = ResearchActivities(worker_database_url, Discovery(), Content(),
        Canonicalizer(), Synthesis(), min_evidence=1, min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(OutputPublisher(
            worker_database_url, TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024))))
    first = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    await execute(activities, first)
    second = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    await activities.prepare_research_activity(ResearchWorkflowInput(1, str(second)))
    with UnitOfWork(database_url) as uow:
        assert authorized_history(uow, second)[0]["run_id"] == str(first)
        grant = uow.execute("SELECT grant_id FROM resource_grants WHERE subject_id=%s "
                            "AND node_id=%s AND operation='read_content' AND revoked_at IS NULL",
                            (task_id, target)).fetchone()["grant_id"]
    ResourcePolicy(database_url).revoke(account_id, grant)
    with UnitOfWork(database_url) as uow, pytest.raises(ResourceDenied):
        authorized_history(uow, second)


@pytest.mark.asyncio
async def test_task_update_content_uses_frozen_revision(
    database_url, worker_database_url, account_id, tmp_path,
):
    catalog = WorkspaceCatalog(database_url)
    tree = catalog.initialize(account_id)
    parent = UUID(next(n["node_id"] for n in tree["nodes"] if n["name"] == "成果"))
    target = catalog.create_directory(account_id, parent, "版本")
    commands = ResearchTaskCommandService(database_url)
    task_id = UUID(commands.create_task(account_id, str(uuid4()), "Daily", "Track facts",
                                        output_directory_id=target,
                                        output_required=True).body["task_id"])
    activities = ResearchActivities(worker_database_url, Discovery(), Content(),
        Canonicalizer(), Synthesis(), min_evidence=1, min_distinct_sources=1,
        markdown_publisher=ResearchMarkdownPublisher(OutputPublisher(
            worker_database_url, TenantFileStore(tmp_path / "store", max_bytes=1024 * 1024))))
    first = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    await execute(activities, first)
    with UnitOfWork(database_url) as uow:
        entry = uow.execute("SELECT entry_id FROM research_run_save_intents WHERE run_id=%s",
                            (first,)).fetchone()["entry_id"]
    catalog.upgrade_file(account_id, entry)
    commands.update_output(account_id, task_id, target, True, "update_content", entry)
    second = UUID(commands.trigger_task(account_id, task_id, str(uuid4())).body["run_id"])
    await execute(activities, second)
    current = catalog.versions(account_id, entry)["current"]
    assert current["revision"] == 2
    with UnitOfWork(database_url) as uow:
        intent = uow.execute("SELECT expected_revision,entry_id,state FROM "
                             "research_run_save_intents WHERE run_id=%s", (second,)).fetchone()
        source = uow.execute("SELECT source_kind,source_run_id FROM "
                             "workspace_version_operations WHERE operation_id=%s",
                             (f"workspace-save:{second}:research_markdown",)).fetchone()
    assert intent["expected_revision"] == 1
    assert intent["entry_id"] == entry and intent["state"] == "succeeded"
    assert source == {"source_kind": "task_auto", "source_run_id": second}
