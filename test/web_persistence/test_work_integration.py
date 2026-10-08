"""Phase 4 vertical contracts using real API/Worker PostgreSQL roles."""

from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep
from uuid import UUID, uuid4

import psycopg
import pytest

from delivery.service import DeliveryService
from orchestration.run_dispatcher import ReminderActivities
from persistence.uow import UnitOfWork
from resources.run_budget import RunBudgetService
from resources.work_budget import WorkBudgetExhausted
from run_domain.lifecycle import RunLifecycleService
from web_artifacts.services import ArtifactService
from web_domain.errors import ResourceNotFound
from work_domain.commands import WorkCommandService
from work_domain.models import Requirement

pytestmark = pytest.mark.postgres


def accept(database_url, account_id, capability="generic_work", criteria=()):
    spec = (
        {"schema_version": 1, "reasoning_mode": "react"}
        if capability == "generic_work"
        else {"schema_version": 1, "content": "Take a break"}
    )
    service = WorkCommandService(database_url)
    result = service.accept(
        account_id,
        str(uuid4()),
        "Phase 4 test",
        Requirement("Do the accepted work", capability, spec, acceptance_criteria=criteria),
    )
    return service, result.body["work"]


def advance(service, work, worker_database_url):
    response = service.advance(
        UUID(work["account_id"]), UUID(work["work_id"]), str(uuid4()), work["row_version"]
    )
    run = response.body["run"]
    RunLifecycleService(worker_database_url).start(UUID(run["account_id"]), UUID(run["run_id"]))
    return run


def test_budget_parallel_reservation_retry_and_late_settlement(
    database_url, worker_database_url, account_id
):
    service, work = accept(database_url, account_id)
    run = advance(service, work, worker_database_url)
    budget = RunBudgetService(worker_database_url)
    with UnitOfWork(worker_database_url) as uow:
        uow.execute(
            "UPDATE work_budgets SET limits=jsonb_set(limits,'{tool_calls}','3') WHERE account_id=%s AND work_id=%s",
            (account_id, UUID(work["work_id"])),
        )

    def reserve(n):
        try:
            budget.reserve(UUID(run["run_id"]), f"parallel:{n}", {"tool_calls": 2})
            return n
        except WorkBudgetExhausted:
            return None

    with ThreadPoolExecutor(2) as pool:
        winners = [n for n in pool.map(reserve, [1, 2]) if n is not None]
    assert len(winners) == 1
    lifecycle = RunLifecycleService(worker_database_url)
    lifecycle.finish(account_id, UUID(run["run_id"]), "failed", failure_code="test_failure")
    # Matching settlement is allowed after terminal state, once only.
    op = f"parallel:{winners[0]}"
    budget.settle(UUID(run["run_id"]), op, {"tool_calls": 2}, "measured")
    budget.settle(UUID(run["run_id"]), op, {"tool_calls": 2}, "measured")
    work = service.get(account_id, UUID(work["work_id"]))["work"]
    retry = advance(service, work, worker_database_url)
    with pytest.raises(WorkBudgetExhausted):
        budget.reserve(UUID(retry["run_id"]), "new-run-does-not-reset", {"tool_calls": 2})
    assert (
        service.get(account_id, UUID(work["work_id"]))["work"]["budget"]["used"]["tool_calls"] == 2
    )


@pytest.mark.asyncio
async def test_reminder_only_completes_after_exact_delivery_receipt(
    database_url, worker_database_url, account_id
):
    service, work = accept(database_url, account_id, "reminder")
    run = advance(service, work, worker_database_url)
    ReminderActivities(worker_database_url)._execute(run["run_id"])
    pending = service.get(account_id, UUID(work["work_id"]))["work"]
    assert pending["status"] == "active" and pending["continuation"]["kind"] == "awaiting_delivery"
    deliveries = DeliveryService(worker_database_url)
    for _ in range(10):
        await deliveries.deliver_once()
        if service.get(account_id, UUID(work["work_id"]))["work"]["status"] == "completed":
            break
    completed = service.get(account_id, UUID(work["work_id"]))["work"]
    assert completed["status"] == "completed"
    assert any(
        d["state"] == "accepted" and d["purpose"] == "fulfillment" for d in completed["deliveries"]
    )
    assert completed["budget"]["used"] == {}


def test_stop_and_uncertain_delivery_preserve_responsibility(
    database_url, worker_database_url, account_id
):
    service, work = accept(database_url, account_id, "reminder")
    run = advance(service, work, worker_database_url)
    ReminderActivities(worker_database_url)._execute(run["run_id"])
    delivery = DeliveryService(worker_database_url)
    deadline = monotonic() + 3
    row = None
    while monotonic() < deadline:
        row = delivery.claim()
        if row and row["purpose"] == "fulfillment":
            break
        if row:
            delivery.finish(row, "accepted", 1, {"level": "account_inbox_committed"})
        else:
            sleep(0.05)
    assert row is not None and row["purpose"] == "fulfillment"
    work = service.get(account_id, UUID(work["work_id"]))["work"]
    stopped = service.control(
        account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"], "stop"
    ).body["work"]
    assert stopped["status"] == "stopping"
    delivery.finish(row, "uncertain", 0, error="lost_sender")
    RunLifecycleService(worker_database_url).converge_controls()
    assert service.get(account_id, UUID(work["work_id"]))["work"]["status"] == "stopping"
    current = service.get(account_id, UUID(work["work_id"]))["work"]
    service.integration(
        account_id,
        UUID(work["work_id"]),
        str(uuid4()),
        current["row_version"],
        "resolve_delivery",
        {"delivery_id": row["delivery_id"], "outcome": "not_sent"},
    )
    for _ in range(10):
        claimed = delivery.claim()
        if claimed:
            assert claimed["purpose"] != "fulfillment"  # Control facts remain deliverable.
            delivery.finish(claimed, "accepted", 1, {"level": "account_inbox_committed"})
    RunLifecycleService(worker_database_url).converge_controls()
    assert service.get(account_id, UUID(work["work_id"]))["work"]["status"] == "stopped"


def test_revision_receipt_cannot_complete_new_requirements(
    database_url, worker_database_url, account_id
):
    service, work = accept(database_url, account_id, "reminder")
    run = advance(service, work, worker_database_url)
    ReminderActivities(worker_database_url)._execute(run["run_id"])
    work = service.get(account_id, UUID(work["work_id"]))["work"]
    revised = service.revise(
        account_id,
        UUID(work["work_id"]),
        str(uuid4()),
        work["row_version"],
        Requirement("New reminder", "reminder", {"schema_version": 1, "content": "Changed"}),
    ).body["work"]
    assert revised["current_requirement_revision"] == 2
    assert any(
        d["state"] == "cancelled" and d["purpose"] == "fulfillment" for d in revised["deliveries"]
    )
    assert revised["status"] == "active"
    with pytest.raises(ResourceNotFound):
        service.get(uuid4(), UUID(work["work_id"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["ready", "retry", "user_and_delivery"])
async def test_artifact_build_has_new_work_run_provenance(
    db, database_url, worker_database_url, account_id, mode
):
    from conversation_domain.commands import CommandService
    from web_artifacts.build import ArtifactBuildService

    chat = CommandService(database_url)
    cid = UUID(
        chat.create_conversation(account_id, str(uuid4())).body["conversation"]["conversation_id"]
    )
    result = chat.send_message(account_id, cid, str(uuid4()), "Prepare an artifact")
    source_run = UUID(result.body["run_id"])
    chat.start_run(account_id, source_run)
    chat.complete_run(account_id, source_run, "# Source answer")
    with UnitOfWork(database_url) as uow:
        message = uow.execute(
            "SELECT message_id FROM messages WHERE account_id=%s AND produced_by_run_id=%s",
            (account_id, source_run),
        ).fetchone()["message_id"]
    created = ArtifactService(database_url).create_artifact(account_id, message, str(uuid4()))
    version = created.body["version"]["artifact_version_id"]
    service = WorkCommandService(database_url)
    work = next(
        w
        for w in service.list(account_id)["items"]
        if w["requirement"]["capability_key"] == "artifact_build"
    )
    if mode == "user_and_delivery":
        criteria = (
            {"id": "artifact", "required": True, "evidence_types": ["artifact_version"]},
            {"id": "channel", "required": True, "evidence_types": ["delivery_receipt"]},
            {"id": "user", "required": True, "evidence_types": ["user_acceptance"]},
        )
        work = service.revise(
            account_id,
            UUID(work["work_id"]),
            str(uuid4()),
            work["row_version"],
            Requirement(
                "Build and confirm the result",
                "artifact_build",
                work["requirement"]["spec"],
                acceptance_criteria=criteria,
            ),
        ).body["work"]
    run = advance(service, work, worker_database_url)

    class Generator:
        async def generate(self, **kwargs):
            assert kwargs["source_markdown"] == "# Source answer"
            return "<html><body>Result</body></html>"

    if mode == "retry":
        from web_artifacts.generator import ArtifactGenerationError

        class FailedGenerator:
            async def generate(self, **kwargs):
                raise ArtifactGenerationError(
                    "artifact_model_timeout", "Retryable timeout", retryable=True
                )

        with pytest.raises(ArtifactGenerationError):
            await ArtifactBuildService(worker_database_url, FailedGenerator()).execute(
                UUID(version), run_id=UUID(run["run_id"])
            )
        RunLifecycleService(worker_database_url).finish(
            account_id, UUID(run["run_id"]), "failed", failure_code="artifact_model_timeout"
        )
        old = ArtifactService(database_url).get_version(account_id, UUID(version))["version"]
        work = service.get(account_id, UUID(work["work_id"]))["work"]
        run = advance(service, work, worker_database_url)
        assert old["producing_run_id"] != run["run_id"] and old["status"] == "failed"
    built = await ArtifactBuildService(worker_database_url, Generator()).execute(
        UUID(version), run_id=UUID(run["run_id"])
    )
    version = built["artifact_version_id"]
    v = ArtifactService(database_url).get_version(account_id, UUID(version))["version"]
    assert v["producing_run_id"] == run["run_id"] and v["producing_run_id"] != str(source_run)
    # SQL privileges do not bypass the provenance/immutable-result triggers.
    for column, replacement in (("html", "forged"), ("source_markdown", "foreign source")):
        with psycopg.connect(worker_database_url) as connection:
            with pytest.raises(psycopg.errors.RaiseException, match="immutable artifact provenance/result") as immutable:
                connection.execute(f"UPDATE hpagent.artifact_versions SET {column}=%s WHERE artifact_version_id=%s",
                                   (replacement, UUID(version)))
            assert immutable.value.sqlstate == "P0001"
    other = uuid4()
    db.execute("INSERT INTO accounts(account_id) VALUES (%s)", (other,))
    with pytest.raises(ResourceNotFound):
        ArtifactService(database_url).create_version(other, UUID(v["artifact_id"]), str(uuid4()), "foreign successor")
    work = service.get(account_id, UUID(work["work_id"]))["work"]
    if mode == "user_and_delivery":
        assert work["status"] == "active" and work["continuation"]["kind"] == "awaiting_delivery"
        sender = DeliveryService(worker_database_url)
        for _ in range(20):
            await sender.deliver_once()
            work = service.get(account_id, UUID(work["work_id"]))["work"]
            if work["continuation"]["kind"] == "awaiting_input":
                break
        assert work["continuation"]["reason"] == "user_acceptance_required"
        # UI-6: a manually generated successor belongs to a new mandate and
        # never supplies acceptance evidence for the original delivery.
        artifacts = ArtifactService(database_url)
        manual_key = str(uuid4())
        manual = artifacts.create_version(
            account_id, UUID(v["artifact_id"]), manual_key, "Manual successor"
        )
        assert artifacts.create_version(
            account_id, UUID(v["artifact_id"]), manual_key, "Manual successor"
        ).replayed
        manual_version = manual.body["version"]["artifact_version_id"]
        assert manual.body["version"]["parent_version_id"] == version
        manual_work = next(
            candidate for candidate in service.list(account_id)["items"]
            if candidate["requirement"]["spec"].get("artifact_version_id") == manual_version
        )
        manual_work = service.revise(
            account_id, UUID(manual_work["work_id"]), str(uuid4()), manual_work["row_version"],
            Requirement("Manual successor requiring review", "artifact_build", manual_work["requirement"]["spec"],
                        acceptance_criteria=(
                            {"id": "artifact", "required": True, "evidence_types": ["artifact_version"]},
                            {"id": "user", "required": True, "evidence_types": ["user_acceptance"]},
                        )),
        ).body["work"]
        manual_run = advance(service, manual_work, worker_database_url)
        await ArtifactBuildService(worker_database_url, Generator()).execute(
            UUID(manual_version), run_id=UUID(manual_run["run_id"])
        )
        original = service.get(account_id, UUID(work["work_id"]))["work"]
        assert [a["artifact_version_id"] for a in original["artifacts"]] == [version]
        for rejected_revision, rejected_version in (
            (work["current_requirement_revision"], manual_version),
            (work["current_requirement_revision"] - 1, version),
        ):
            with pytest.raises(ValueError):
                service.integration(
                    account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"],
                    "accept_result", {"requirement_revision": rejected_revision,
                                      "artifact_version_id": UUID(rejected_version)},
                )
        # Advancing the new mandate's control epoch through public commands
        # invalidates its old successful execution without editing Run facts.
        manual_work = service.get(account_id, UUID(manual_work["work_id"]))["work"]
        for action in ("pause", "resume"):
            manual_work = service.control(
                account_id, UUID(manual_work["work_id"]), str(uuid4()), manual_work["row_version"], action
            ).body["work"]
        with pytest.raises(ValueError, match="not awaiting explicit user acceptance"):
            service.integration(
                account_id, UUID(manual_work["work_id"]), str(uuid4()), manual_work["row_version"],
                "accept_result", {"requirement_revision": manual_work["current_requirement_revision"],
                                  "artifact_version_id": UUID(manual_version)},
            )
        work = service.integration(
            account_id,
            UUID(work["work_id"]),
            str(uuid4()),
            work["row_version"],
            "accept_result",
            {
                "requirement_revision": work["current_requirement_revision"],
                "artifact_version_id": UUID(version),
            },
        ).body["work"]
    assert work["status"] == "completed" and any(
        a["accepted_for_revision"] == work["current_requirement_revision"]
        for a in work["artifacts"]
    )
    if mode == "ready":
        # A new Work evaluates an explicitly frozen version without rewriting its producer.
        service, reuse = accept(
            database_url,
            account_id,
            criteria=(
                {"id": "artifact", "required": True, "evidence_types": ["artifact_version"]},
            ),
        )
        reuse = service.integration(
            account_id,
            UUID(reuse["work_id"]),
            str(uuid4()),
            reuse["row_version"],
            "artifact_reference",
            {"artifact_version_id": UUID(version), "role": "evidence"},
        ).body["work"]
        reuse_run = advance(service, reuse, worker_database_url)
        assert reuse_run["input_snapshot"]["artifact_refs"] == [
            {"artifact_version_id": version, "role": "evidence"}
        ]
        RunLifecycleService(worker_database_url).finish(
            account_id,
            UUID(reuse_run["run_id"]),
            "succeeded",
            accept_result=True,
            result={
                "schema_version": 1,
                "kind": "deliverable_ready",
                "evidence": [
                    {"criterion_id": "artifact", "type": "artifact_version", "ref": version}
                ],
            },
        )
        reused = service.get(account_id, UUID(reuse["work_id"]))["work"]
        assert reused["status"] == "completed"
        assert any(a["accepted_for_revision"] == 1 for a in reused["artifacts"])
        assert (
            ArtifactService(database_url).get_version(account_id, UUID(version))["version"][
                "producing_run_id"
            ]
            == run["run_id"]
        )


def test_resume_revalidates_accepted_reminder_without_resending(
    database_url, worker_database_url, account_id
):
    service, work = accept(database_url, account_id, "reminder")
    run = advance(service, work, worker_database_url)
    ReminderActivities(worker_database_url)._execute(run["run_id"])
    sender = DeliveryService(worker_database_url)
    for _ in range(10):
        row = sender.claim()
        if row and row["purpose"] == "fulfillment":
            break
        if row:
            sender.finish(row, "accepted", 1, {"level": "account_inbox_committed"})
    assert row["purpose"] == "fulfillment"
    current = service.get(account_id, UUID(work["work_id"]))["work"]
    service.control(
        account_id, UUID(work["work_id"]), str(uuid4()), current["row_version"], "pause"
    )
    sender.finish(row, "accepted", 1, {"level": "account_inbox_committed"})
    RunLifecycleService(worker_database_url).converge_controls()
    paused = service.get(account_id, UUID(work["work_id"]))["work"]
    assert paused["status"] == "paused"
    resumed = service.control(
        account_id, UUID(work["work_id"]), str(uuid4()), paused["row_version"], "resume"
    ).body["work"]
    retry = advance(service, resumed, worker_database_url)
    ReminderActivities(worker_database_url)._execute(retry["run_id"])
    completed = service.get(account_id, UUID(work["work_id"]))["work"]
    assert completed["status"] == "completed"
    assert any(
        d["provider_receipt"]
        and d["provider_receipt"].get("revalidated_from_delivery_id") == str(row["delivery_id"])
        for d in completed["deliveries"]
    )


def test_capacity_reserves_interactive_room_and_recovers_lease(
    database_url, worker_database_url, account_id, db, monkeypatch
):
    from resources.capacity import CapacityService

    monkeypatch.setenv("CAPACITY_MODEL_GLOBAL", "3")
    monkeypatch.setenv("CAPACITY_MODEL_INTERACTIVE_RESERVED", "1")
    monkeypatch.setenv("CAPACITY_MODEL_ACCOUNT", "3")
    service, first = accept(database_url, account_id)
    _, second = accept(database_url, account_id)
    _, third = accept(database_url, account_id)
    runs = [advance(service, w, worker_database_url) for w in [first, second, third]]
    capacity = CapacityService(worker_database_url)
    tickets = [uuid4() for _ in runs]
    assert capacity.acquire(UUID(runs[0]["run_id"]), "model", tickets[0])
    assert capacity.acquire(UUID(runs[1]["run_id"]), "model", tickets[1])
    assert not capacity.acquire(UUID(runs[2]["run_id"]), "model", tickets[2])
    # A waiting ticket holds no physical slot, and an expired held ticket is recovered.
    db.execute(
        "UPDATE capacity_queue SET lease_until=now()-interval '1 second' WHERE ticket_id=%s",
        (tickets[0],),
    )
    assert capacity.acquire(UUID(runs[2]["run_id"]), "model", tickets[2])
    assert not capacity.renew(tickets[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", ["private", "group"])
async def test_qq_reminder_target_is_explicit_and_work_control_is_model_free(
    database_url, worker_database_url, account_id, db, scope
):
    from support.qq_messages import qq_message

    from application.qq_delivery import QQDeliveryAdapter
    from web_persistence.test_qq_canonical_ingress import bind
    from web_persistence.test_qq_canonical_ingress import service as qq_service

    bind(db, account_id)
    qq = qq_service(worker_database_url)
    ingress = await qq.accept(qq_message("work-reminder", "Remind me here", scope=scope), "napcat")
    source = db.execute(
        "SELECT trigger_message_id FROM runs WHERE run_id=%s", (UUID(ingress["run_id"]),)
    ).fetchone()[0]
    commands = WorkCommandService(database_url)
    accepted = commands.accept(
        account_id,
        str(uuid4()),
        "QQ reminder",
        Requirement(
            "Send reminder",
            "reminder",
            {"schema_version": 1, "content": "Time for a break", "target_ref": "current_channel"},
        ),
        source_message_id=source,
    )
    work = accepted.body["work"]
    assert work["requirement"]["spec"]["target_ref"] != "current_channel"
    run = advance(commands, work, worker_database_url)
    ReminderActivities(worker_database_url)._execute(run["run_id"])

    class Router:
        sent = []

        async def send(self, message):
            self.sent.append(message)
            return True

    router = Router()
    sender = DeliveryService(worker_database_url, QQDeliveryAdapter(router))
    for _ in range(10):
        await sender.deliver_once()
        current = commands.get(account_id, UUID(work["work_id"]))["work"]
        if current["status"] == "completed":
            break
    assert current["status"] == "completed" and len(router.sent) == 1
    if scope == "group":
        assert "Time for a break" not in router.sent[0].content
        assert work["work_id"] in router.sent[0].content
    else:
        assert "Time for a break" in router.sent[0].content
    count = db.execute("SELECT count(*) FROM runs").fetchone()[0]
    status = await qq.accept(
        qq_message("work-status", f"/work {work['work_id']} status", scope=scope), "napcat"
    )
    assert status["work_control"]["status"] == "completed"
    assert db.execute("SELECT count(*) FROM runs").fetchone()[0] == count


def test_explicit_input_revoke_blocks_dispatch_and_budget_adjustment_is_versioned(
    database_url, worker_database_url, account_id, db
):
    from web_domain.file_lifecycle import claim_file_deletion
    from workspace.catalog import WorkspaceCatalog
    from workspace.resources import ResourceDenied, ResourcePolicy

    service, work = accept(database_url, account_id)
    tree = WorkspaceCatalog(database_url).initialize(account_id)
    fid = uuid4()
    db.execute(
        "INSERT INTO stored_files(file_id,account_id,source_workspace_id,purpose,status,original_name,display_name,storage_key,content_type,size_bytes,sha256,ready_at) "
        "VALUES (%s,%s,%s,'input','ready','work.txt','work.txt','phase4-input','text/plain',1,%s,now())",
        (fid, account_id, UUID(tree["workspace_id"]), "a" * 64),
    )
    key = str(uuid4())
    added = service.integration(
        account_id,
        UUID(work["work_id"]),
        key,
        work["row_version"],
        "input",
        {"file_id": fid, "purpose": "Accepted evidence"},
    )
    replay = service.integration(
        account_id,
        UUID(work["work_id"]),
        key,
        work["row_version"],
        "input",
        {"file_id": fid, "purpose": "Accepted evidence"},
    )
    assert replay.replayed and replay.body == added.body
    work = added.body["work"]
    run = advance(service, work, worker_database_url)
    policy = ResourcePolicy(worker_database_url)
    policy.check_dispatch(account_id, UUID(run["run_id"]))
    with UnitOfWork(database_url) as uow:
        ref = uow.execute(
            "SELECT ref_id FROM work_input_refs WHERE work_id=%s", (UUID(work["work_id"]),)
        ).fetchone()["ref_id"]
        assert claim_file_deletion(uow, account_id, fid) == "bound"
    work = service.get(account_id, UUID(work["work_id"]))["work"]
    changed = service.integration(
        account_id,
        UUID(work["work_id"]),
        str(uuid4()),
        work["row_version"],
        "revoke_input",
        {"ref_id": ref},
    )
    with pytest.raises(ResourceDenied):
        policy.check_dispatch(account_id, UUID(run["run_id"]))
    work = changed.body["work"]
    old_budget = work["budget"]
    changed = service.integration(
        account_id,
        UUID(work["work_id"]),
        str(uuid4()),
        work["row_version"],
        "budget",
        {
            "budget_version": old_budget["version"],
            "limits": {"tool_calls": old_budget["limits"]["tool_calls"] + 10},
        },
    )
    assert changed.body["work"]["budget"]["version"] == old_budget["version"] + 1
    assert changed.body["work"]["budget"]["used"] == old_budget["used"]
