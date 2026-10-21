"""S4 composition uses isolated PostgreSQL and deterministic models, no external requests."""

from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from test_capability_facts import chat, execution

from agent_activities.capabilities import CapabilityActivities, CapabilityBinding
from agent_activities.store import AgentDataStore
from agent_workflows.capability_contracts import CapabilityClose, CapabilityExecutionInput
from file_runtime.output import OutputPublisher
from persistence.uow import UnitOfWork
from run_domain.acceptance import ResultAdoptionService
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import AcceptancePredicate, CapabilityRegistry
from storage.tenant_file_store import TenantFileStore
from web_artifacts.build import ArtifactBuildService
from web_artifacts.capability import html_definition, html_scope
from web_artifacts.generator import WebArtifactGenerator
from web_artifacts.local import HtmlActivities
from web_artifacts.services import ArtifactService
from workspace.resources import ResourceDenied

pytestmark = [pytest.mark.postgres, pytest.mark.asyncio]


class Model:
    calls = 0
    fail = False

    async def generate(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content="invalid"
            if self.fail
            else f"<html><head></head><body>result {self.calls}</body></html>"
        )


def setup(urls, tmp_path):
    caps = CapabilityActivities(
        AgentDataStore(urls[2]),
        CapabilityFacts(CapabilityRegistry((html_definition(),)), services=frozenset({"html"})),
        (CapabilityBinding("html", 1, "HtmlCapabilityWorkflow", html_scope),),
    )
    publisher = OutputPublisher(
        urls[2], TenantFileStore(tmp_path / "files", max_bytes=2 * 1024 * 1024)
    )
    model = Model()
    return caps, HtmlActivities(caps, WebArtifactGenerator(model), publisher), model


def admit(caps, urls, account, run, inputs):
    operation = f"{run}:html:{uuid4()}"
    with execution(urls, account, run) as (store, _, _):
        store.begin_operation(operation, str(run), "tool")
        with UnitOfWork(urls[2]) as uow:
            row = caps.facts.create(
                uow,
                operation_id=operation,
                capability_key="html",
                contract_version=1,
                inputs=inputs,
                scope=html_scope(inputs),
                invocation_limits=html_definition().limits,
            )
    return CapabilityExecutionInput(
        str(account),
        str(run),
        str(row["child_execution_id"]),
        operation,
        "HtmlCapabilityWorkflow",
        600,
        3,
        operation + ":produce",
    )


async def produce(caps, html, urls, account, run, child):
    with execution(urls, account, run, child.execution_id) as (_, _, token):
        await html.produce(replace(child, lease_token=token))
        await html.produce(replace(child, lease_token=token))
    await caps.close(CapabilityClose(child, "succeeded"))
    with execution(urls, account, run):
        with UnitOfWork(urls[2]) as uow:
            return caps.facts.read_result(uow, child.invocation_id)


async def test_html_chat_versions_exact_parent_replay_and_local_failure(
    urls, owner, account_id, tmp_path
):
    run = chat(urls, account_id)
    caps, html, model = setup(urls, tmp_path)
    first = await produce(
        caps, html, urls, account_id, run, admit(caps, urls, account_id, run, {"text": "# Report"})
    )
    ref = first["outputs"][0]
    assert first["checks"][0]["subject_ref"] == ref["ref_id"]
    assert "not checked" in first["summary"]
    model.fail = True
    failed = await produce(
        caps,
        html,
        urls,
        account_id,
        run,
        admit(caps, urls, account_id, run, {"text": "# Report", "previous_ref": ref["ref_id"]}),
    )
    assert failed["outcome"] == "insufficient"
    model.fail = False
    second = await produce(
        caps,
        html,
        urls,
        account_id,
        run,
        admit(caps, urls, account_id, run, {"text": "# Report", "previous_ref": ref["ref_id"]}),
    )
    assert second["outputs"][0]["object_id"] == ref["object_id"]
    assert second["outputs"][0]["version"] != ref["version"]
    # An explicit older parent stays fixed even after another completed version exists.
    third = await produce(
        caps,
        html,
        urls,
        account_id,
        run,
        admit(caps, urls, account_id, run, {"text": "# Report", "previous_ref": ref["ref_id"]}),
    )
    assert third["outputs"][0]["version"] != second["outputs"][0]["version"]
    versions = owner.execute(
        "SELECT version,parent_version_id FROM artifact_versions WHERE artifact_id=%s ORDER BY version",
        (UUID(ref["object_id"]),),
    ).fetchall()
    assert [v["version"] for v in versions] == [1, 2, 3]
    assert versions[1]["parent_version_id"] == UUID(ref["version"])
    assert versions[2]["parent_version_id"] == UUID(ref["version"])
    assert model.calls == 4
    assert (
        owner.execute(
            "SELECT count(*) AS n FROM works WHERE account_id=%s", (account_id,)
        ).fetchone()["n"]
        == 0
    )


async def test_explicit_artifact_api_uses_chat_without_work(urls, owner, account_id, tmp_path):
    from run_domain.lifecycle import RunLifecycleService

    run = chat(urls, account_id)
    RunLifecycleService(urls[2]).finish(account_id, run, "succeeded", content="# Source")
    message = owner.execute(
        "SELECT message_id FROM messages WHERE produced_by_run_id=%s", (run,)
    ).fetchone()["message_id"]
    service = ArtifactService(urls[1])
    key = str(uuid4())
    created = service.create_artifact(account_id, message, key)
    assert service.create_artifact(account_id, message, key).replayed
    pending = created.body["version"]
    build_run = owner.execute(
        "SELECT run_id FROM runs WHERE account_id=%s AND executor_key='artifact_html'",
        (account_id,),
    ).fetchone()["run_id"]
    RunLifecycleService(urls[2]).start(account_id, build_run)
    caps, html, model = setup(urls, tmp_path)
    build = ArtifactBuildService(urls[2], html.generator, caps, html.publisher)
    result = await build.execute(UUID(pending["artifact_version_id"]), run_id=build_run)
    assert result["status"] == "completed"
    assert (
        owner.execute(
            "SELECT count(*) AS n FROM works WHERE account_id=%s", (account_id,)
        ).fetchone()["n"]
        == 0
    )
    version = service.get_version(account_id, UUID(result["artifact_version_id"]))["version"]
    assert (
        version["producing_execution_id"] != caps.store.run_identity(str(build_run))["execution_id"]
    )
    assert version["result_ref_id"] == version["checks"][0]["subject_ref"]
    assert version["checks"][0]["verifier_key"] == "html.basic"
    assert version["producing_invocation_id"].startswith("html:")
    assert version["result_outcome"] == "succeeded"
    assert "not checked" in version["result_summary"]
    # A second explicit operation fixes the exact prior version and uses the shared generator.
    new = service.create_version(
        account_id, UUID(version["artifact_id"]), str(uuid4()), "change color"
    )
    following = owner.execute(
        "SELECT run_id FROM runs WHERE account_id=%s AND status='queued'", (account_id,)
    ).fetchone()["run_id"]
    RunLifecycleService(urls[2]).start(account_id, following)
    await build.execute(UUID(new.body["version"]["artifact_version_id"]), run_id=following)
    assert model.calls == 2
    model.fail = True
    failed_request = service.create_version(
        account_id, UUID(version["artifact_id"]), str(uuid4()), "Malformed output"
    )
    failing_run = owner.execute(
        "SELECT run_id FROM runs WHERE account_id=%s AND status='queued'", (account_id,)
    ).fetchone()["run_id"]
    RunLifecycleService(urls[2]).start(account_id, failing_run)
    failed = await build.execute(
        UUID(failed_request.body["version"]["artifact_version_id"]), run_id=failing_run
    )
    assert failed["status"] == "failed"
    view = service.get_version(
        account_id, UUID(failed_request.body["version"]["artifact_version_id"])
    )["version"]
    assert view["status"] == "failed" and view["result_outcome"] == "insufficient"
    assert view["checks"] == [] and view["result_ref_id"] is None
    assert view["producing_invocation_id"]


async def test_report_to_html_cross_run_adoption_revocation_and_gc(
    urls, owner, account_id, tmp_path
):
    from test_research_capability import admit as research_admit
    from test_research_capability import produce as research_produce
    from test_research_capability import setup as research_setup

    from web_domain.file_lifecycle import claim_file_deletion

    run = chat(urls, account_id)
    research, research_caps = research_setup(urls, tmp_path)
    request = research_admit(research_caps, urls, account_id, run, "source")
    await research_produce(research, request)
    with execution(urls, account_id, run):
        with UnitOfWork(urls[2]) as uow:
            report = research_caps.facts.read_result(uow, request.invocation_id)
    markdown = report["outputs"][0]
    caps, html, model = setup(urls, tmp_path)
    # Read the actual committed Markdown bytes, without embedding the report in a tool argument.
    result = await produce(
        caps,
        html,
        urls,
        account_id,
        run,
        admit(caps, urls, account_id, run, {"source_ref": markdown["ref_id"]}),
    )
    assert result["outcome"] == "succeeded"
    other = chat(urls, account_id)
    with execution(urls, account_id, other):
        with pytest.raises(ResourceDenied):
            ResultAdoptionService(urls[2], caps.facts).adopt(markdown["ref_id"])
    cid = owner.execute("SELECT conversation_id FROM runs WHERE run_id=%s", (other,)).fetchone()[
        "conversation_id"
    ]
    owner.execute(
        "INSERT INTO conversation_resource_files(account_id,conversation_id,file_id) VALUES (%s,%s,%s)",
        (account_id, cid, UUID(markdown["object_id"])),
    )
    owner.execute(
        "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,basis) VALUES (%s,%s,%s,%s,'explicit_attachment')",
        (uuid4(), account_id, other, UUID(markdown["object_id"])),
    )
    with execution(urls, account_id, other):
        adopted = ResultAdoptionService(urls[2], caps.facts).adopt(markdown["ref_id"])
        assert adopted["producing_invocation_id"] == request.invocation_id
    reused = await produce(
        caps,
        html,
        urls,
        account_id,
        other,
        admit(caps, urls, account_id, other, {"source_ref": markdown["ref_id"]}),
    )
    assert reused["outcome"] == "succeeded"
    child = admit(caps, urls, account_id, other, {"source_ref": markdown["ref_id"]})
    owner.execute(
        "UPDATE conversation_resource_files SET available=false WHERE account_id=%s AND conversation_id=%s AND file_id=%s",
        (account_id, cid, UUID(markdown["object_id"])),
    )
    with execution(urls, account_id, other, child.execution_id) as (_, _, token):
        with pytest.raises(ResourceDenied):
            await html.produce(replace(child, lease_token=token))
    assert model.calls == 2
    with UnitOfWork(urls[0]) as uow:
        assert claim_file_deletion(uow, account_id, UUID(markdown["object_id"])) == "bound"
    assert (
        owner.execute(
            "SELECT run_id FROM capability_results WHERE invocation_id=%s", (request.invocation_id,)
        ).fetchone()["run_id"]
        == run
    )


@pytest.mark.parametrize("review", [False, True])
async def test_work_html_typed_checks_and_parent_save(urls, owner, account_id, tmp_path, review):
    import json

    from application.work_context import generic_result
    from run_domain.lifecycle import RunLifecycleService
    from work_domain.commands import WorkCommandService
    from work_domain.models import Requirement
    from work_domain.persistence import WorkRepository
    from workspace.catalog import WorkspaceCatalog
    from workspace.resources import ResourcePolicy

    catalog = WorkspaceCatalog(urls[1])
    tree = catalog.initialize(account_id)
    directory = UUID(tree["root_id"])
    commands = WorkCommandService(urls[1])
    work = commands.accept(
        account_id,
        str(uuid4()),
        "Compose HTML",
        Requirement(
            "Make HTML",
            "generic_work",
            {"schema_version": 1},
            acceptance_criteria=(
                {"id": "html", "required": True, "evidence_types": ["check_passed"]},
                {"id": "save", "required": True, "evidence_types": ["workspace_saved"]},
            )
            + (
                ({"id": "review", "required": True, "evidence_types": ["user_acceptance"]},)
                if review
                else ()
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
    caps, html, _ = setup(urls, tmp_path)
    result = await produce(
        caps, html, urls, account_id, run, admit(caps, urls, account_id, run, {"text": "# Report"})
    )
    subject = result["outputs"][0]["ref_id"]
    service = ResultAdoptionService(urls[2], caps.facts)
    with execution(urls, account_id, run):
        with pytest.raises(ValueError):
            service.evidence(
                "html", AcceptancePredicate("check_passed", subject, "html.basic", "wrong-version")
            )
        check = service.evidence(
            "html", AcceptancePredicate("check_passed", subject, "html.basic", "1")
        )
        saved = service.save(
            subject, directory_id=str(directory), name="result.html", operation_id="save-html"
        )
        assert (
            service.save(
                subject, directory_id=str(directory), name="result.html", operation_id="save-html"
            )
            == saved
        )
        evidence = service.evidence(
            "save",
            AcceptancePredicate(
                "workspace_saved", subject, target_id=saved["node_id"], operation_id="save-html"
            ),
        )
        with UnitOfWork(urls[2]) as uow:
            current_run = dict(uow.execute("SELECT * FROM runs WHERE run_id=%s", (run,)).fetchone())
            current_work = WorkRepository.get(uow, account_id, wid)
            with pytest.raises(ValueError, match="typed"):
                generic_result(
                    uow,
                    current_run,
                    current_work,
                    json.dumps(
                        {
                            "schema_version": 1,
                            "kind": "deliverable_ready",
                            "evidence": [
                                {
                                    "criterion_id": "html",
                                    "type": "operation_receipt",
                                    "ref": "unrelated-success",
                                }
                            ],
                        }
                    ),
                    "result",
                )
            completion, ready = generic_result(
                uow,
                current_run,
                current_work,
                json.dumps(
                    {
                        "schema_version": 1,
                        "kind": "deliverable_ready",
                        "evidence": [check, evidence],
                    }
                ),
                "result",
            )
            assert ready is (not review)
            if review:
                assert completion["continuation"]["reason"] == "user_acceptance_required"
            RunLifecycleService(urls[2]).finish_in_uow(
                uow, current_run, "succeeded", result=completion, accept_result=ready
            )
    if review:
        waiting = commands.get(account_id, wid)["work"]
        assert waiting["status"] == "active"
        commands.integration(
            account_id,
            wid,
            str(uuid4()),
            waiting["row_version"],
            "accept_result",
            {
                "requirement_revision": waiting["current_requirement_revision"],
                "artifact_version_id": UUID(result["outputs"][0]["version"]),
            },
        )
    assert commands.get(account_id, wid)["work"]["status"] == "completed"


async def test_failed_run_result_adoption_updates_only_frozen_workspace_revision(
    urls, owner, account_id, tmp_path
):
    from conversation_domain.commands import CommandService
    from run_domain.lifecycle import RunLifecycleService
    from workspace.catalog import WorkspaceCatalog, WorkspaceVersionConflict
    from workspace.resources import ResourcePolicy

    source = chat(urls, account_id)
    caps, html, _ = setup(urls, tmp_path)
    outputs = []
    for i in range(2):
        result = await produce(
            caps,
            html,
            urls,
            account_id,
            source,
            admit(caps, urls, account_id, source, {"text": f"# Version {i}"}),
        )
        ref = result["outputs"][0]
        file_id = owner.execute(
            "SELECT file_id FROM artifact_versions WHERE artifact_version_id=%s",
            (UUID(ref["version"]),),
        ).fetchone()["file_id"]
        outputs.append((ref, file_id))
    catalog = WorkspaceCatalog(urls[1])
    root = UUID(catalog.initialize(account_id)["root_id"])
    target = catalog.save_file(account_id, root, outputs[0][1], "target.html", "target-initial")
    source_entry = catalog.save_file(
        account_id, root, outputs[1][1], "source.html", "source-initial"
    )
    initial = catalog.upgrade_file(account_id, target)
    RunLifecycleService(urls[2]).finish(
        account_id, source, "failed", failure_code="later_parent_failed"
    )
    commands = CommandService(urls[1])
    cid = UUID(commands.create_conversation(account_id, str(uuid4())).body["conversation_id"])
    policy = ResourcePolicy(urls[1])
    policy.grant(
        account_id,
        "conversation",
        cid,
        target,
        ["list_metadata", "read_content", "update_content"],
        False,
    )
    policy.grant(
        account_id, "conversation", cid, source_entry, ["list_metadata", "read_content"], False
    )
    consumer = UUID(
        commands.send_message(account_id, cid, str(uuid4()), "Adopt and update").body["run_id"]
    )
    policy.select(account_id, consumer, target)
    policy.select(account_id, consumer, source_entry)
    RunLifecycleService(urls[2]).start(account_id, consumer)
    service = ResultAdoptionService(urls[2], caps.facts)
    with execution(urls, account_id, consumer):
        service.adopt(outputs[1][0]["ref_id"])
        args = dict(entry_id=str(target), expected_revision=1, expected_sha256=initial["sha256"])
        updated = service.save(outputs[1][0]["ref_id"], operation_id="adopted-update", **args)
        assert updated["revision"] == 2
        assert updated["file_id"] == str(outputs[1][1])
        assert (
            service.save(outputs[1][0]["ref_id"], operation_id="adopted-update", **args) == updated
        )
        with pytest.raises(WorkspaceVersionConflict):
            service.save(outputs[1][0]["ref_id"], operation_id="stale-update", **args)
    assert (
        owner.execute(
            "SELECT producing_run_id FROM artifact_versions WHERE artifact_version_id=%s",
            (UUID(outputs[1][0]["version"]),),
        ).fetchone()["producing_run_id"]
        == source
    )
    assert catalog.versions(account_id, target)["current"]["revision"] == 2
