"""Revision protection and quiet daily results using real API/worker roles."""

from contextvars import Context
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import psycopg
import pytest
from test_capability_contracts import definition
from test_capability_facts import begin, execution, output_file

from orchestration.work_schedule import WorkScheduleService
from persistence.uow import UnitOfWork
from run_domain.admission import admit_work_run
from run_domain.capabilities import CapabilityFacts
from run_domain.capability_contracts import CapabilityRegistry, CapabilityResult, ResultRef
from run_domain.lifecycle import RunLifecycleService
from work_domain.commands import WorkConflict
from work_domain.models import Requirement, continuation
from work_domain.persistence import WorkRepository

pytestmark = pytest.mark.postgres


@pytest.fixture(autouse=True)
def finish_test_works(commands, account_id):
    yield
    for work in commands.list(account_id)["items"]:
        if work["status"] in {"active", "paused", "pausing"}:
            commands.control(
                account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"], "stop"
            )


@pytest.fixture
def facts():
    return CapabilityFacts(CapabilityRegistry((definition(),)))


def accept(commands, account, *, daily=False):
    req = Requirement(
        "Observe facts", "generic_work", {"schema_version": 1}, completion_mode="ongoing"
    )
    if daily:
        req = replace(
            req,
            timing={"schema_version": 1, "kind": "daily", "timezone": "UTC", "local_time": "09:00"},
        )
    work = commands.accept(account, str(uuid4()), "Observe", req).body["work"]
    return UUID(work["work_id"])


def start(commands, account, work_id, urls, owner):
    work = commands.get(account, work_id)["work"]
    if work["continuation"].get("due_at"):
        # Fixture clock: only the dedicated test Work is made due.
        owner.execute(
            "UPDATE works SET continuation=%s::jsonb,row_version=row_version+1 WHERE work_id=%s",
            ('{"schema_version":1,"kind":"ready","reason":"test_due"}', work_id),
        )
        work = commands.get(account, work_id)["work"]
    run = UUID(
        commands.advance(account, work_id, str(uuid4()), work["row_version"]).body["run"]["run_id"]
    )
    RunLifecycleService(urls[2]).start(account, run)
    return run


def result(facts, urls, owner, account, run, *, outcome="succeeded", content=None, text="same"):
    with execution(urls, account, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, str(uuid4()), inputs={"text": text})
    producer = invocation["invocation_id"] + ":producer"
    outputs = ()
    if content:
        file_id = output_file(owner, account, run)
        owner.execute("UPDATE stored_files SET sha256=%s WHERE file_id=%s", (content * 64, file_id))
        outputs = (
            ResultRef(
                str(uuid4()),
                "markdown",
                str(file_id),
                str(file_id),
                invocation["invocation_id"],
                str(invocation["child_execution_id"]),
                producer,
                content * 64,
            ),
        )
    payload = CapabilityResult(outcome, str(uuid4()), outputs=outputs)
    with execution(urls, account, run, invocation["child_execution_id"]) as (store, _, _):
        store.begin_operation(producer, str(run), "result")
        for _ in range(2):
            with UnitOfWork(urls[2]) as uow:
                facts.publish_result(
                    uow,
                    invocation_id=invocation["invocation_id"],
                    producing_operation_id=producer,
                    result=payload,
                )
    return invocation


def finish(urls, account, run, *, status="succeeded"):
    return RunLifecycleService(urls[2]).finish(
        account,
        run,
        status,
        result={"schema_version": 1, "kind": "progress_saved", "evidence": []}
        if status == "succeeded"
        else None,
        failure_code="test_failure" if status == "failed" else None,
    )


@pytest.mark.parametrize(
    "outcome,reason", [("insufficient", "repeated_failure"), ("succeeded", "no_progress")]
)
def test_protection_survives_runs_replay_and_scheduler(
    commands, account_id, urls, owner, facts, outcome, reason
):
    work_id = accept(commands, account_id)
    for index in range(3):
        run = start(commands, account_id, work_id, urls, owner)
        result(facts, urls, owner, account_id, run, outcome=outcome)
        finish(urls, account_id, run)
        work = commands.get(account_id, work_id)["work"]
        assert work["progress"]["no_progress_count"] == index + 1
    assert work["continuation"] == continuation("blocked", reason)
    assert work["progress"]["blocked_reason"] == reason
    assert work["status"] == "active"
    with pytest.raises(WorkConflict):
        commands.advance(account_id, work_id, str(uuid4()), work["row_version"])
    with UnitOfWork(urls[2]) as uow:
        current = WorkRepository.get(uow, account_id, work_id, lock=True)
        requirement = WorkRepository.requirement(uow, account_id, work_id, 1)
        assert admit_work_run(uow, current, requirement, None, urls[2], "enforce") is None
    assert commands.get(account_id, work_id)["work"]["active_coordinator_run_id"] is None
    assert (
        owner.execute(
            "SELECT count(*) n FROM work_progress_samples WHERE work_id=%s", (work_id,)
        ).fetchone()["n"]
        == 3
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM notifications WHERE work_id=%s", (work_id,)
        ).fetchone()["n"]
        == 1
    )
    # Explicit pause/resume is an audited recovery, not a timer reset.
    paused = commands.control(account_id, work_id, str(uuid4()), work["row_version"], "pause").body[
        "work"
    ]
    with UnitOfWork(urls[2]) as uow:
        current = WorkRepository.get(uow, account_id, work_id, lock=True)
        requirement = WorkRepository.requirement(uow, account_id, work_id, 1)
        assert admit_work_run(uow, current, requirement, None, urls[2], "enforce") is None
    assert commands.get(account_id, work_id)["work"]["active_coordinator_run_id"] is None
    resumed = commands.control(
        account_id, work_id, str(uuid4()), paused["row_version"], "resume"
    ).body["work"]
    assert resumed["progress"]["blocked_reason"] is None
    assert resumed["progress"]["no_progress_count"] == 0


def test_new_content_not_summary_or_ref_ids_resets_progress(
    commands, account_id, urls, owner, facts
):
    work_id = accept(commands, account_id)
    for content, expected in [("a", 0), ("a", 1), ("b", 0)]:
        run = start(commands, account_id, work_id, urls, owner)
        result(facts, urls, owner, account_id, run, content=content)
        finish(urls, account_id, run)
        assert (
            commands.get(account_id, work_id)["work"]["progress"]["no_progress_count"] == expected
        )
    assert (
        owner.execute(
            "SELECT count(*) n FROM work_progress_facts WHERE work_id=%s", (work_id,)
        ).fetchone()["n"]
        == 2
    )


def test_daily_unchanged_is_quiet_failed_result_keeps_fixed_baseline(
    commands, account_id, urls, owner, facts
):
    work_id = accept(commands, account_id, daily=True)
    runs = []
    for index, (content, outcome) in enumerate(
        [
            ("a", "succeeded"),
            ("a", "succeeded"),
            ("b", "insufficient"),
            ("b", "succeeded"),
            ("b", "succeeded"),
        ]
    ):
        run = start(commands, account_id, work_id, urls, owner)
        runs.append(run)
        result(facts, urls, owner, account_id, run, outcome=outcome, content=content)
        finish(urls, account_id, run)
        finish(urls, account_id, run)  # Lost completion response, no duplicate notification.
        work = commands.get(account_id, work_id)["work"]
        assert work["status"] == "active"
        assert work["progress"]["blocked_reason"] is None
        assert work["daily_baseline"]["run_id"] == str(runs[1] if index == 2 else run)
    rows = owner.execute(
        "SELECT * FROM work_daily_results WHERE work_id=%s ORDER BY created_at", (work_id,)
    ).fetchall()
    assert [r["changed"] for r in rows] == [True, False, True, False]
    assert rows[2]["previous_run_id"] == runs[1]
    assert (
        owner.execute(
            "SELECT count(*) n FROM notifications WHERE work_id=%s", (work_id,)
        ).fetchone()["n"]
        == 2
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM work_daily_result_refs WHERE run_id=%s", (runs[-1],)
        ).fetchone()["n"]
        == 1
    )
    # API can query, but cannot forge baseline or progress samples.
    with UnitOfWork(urls[1]) as uow, pytest.raises(psycopg.errors.InsufficientPrivilege):
        uow.execute("UPDATE work_progress SET no_progress_count=0 WHERE work_id=%s", (work_id,))


def test_budget_cannot_resume_or_advance_without_actual_increase(commands, account_id, urls, owner):
    work_id = accept(commands, account_id)
    run = start(commands, account_id, work_id, urls, owner)
    RunLifecycleService(urls[2]).finish(
        account_id, run, "failed", failure_code="work_budget_exhausted"
    )
    work = commands.get(account_id, work_id)["work"]
    assert work["continuation"]["reason"] == "budget_exhausted"
    paused = commands.control(account_id, work_id, str(uuid4()), work["row_version"], "pause").body[
        "work"
    ]
    work = commands.control(
        account_id, work_id, str(uuid4()), paused["row_version"], "resume"
    ).body["work"]
    assert work["continuation"]["reason"] == "budget_exhausted"
    with pytest.raises(WorkConflict):
        commands.advance(account_id, work_id, str(uuid4()), work["row_version"])
    for increment in (0, 1):
        budget = work["budget"]
        work = commands.integration(
            account_id,
            work_id,
            str(uuid4()),
            work["row_version"],
            "budget",
            {
                "budget_version": budget["version"],
                "limits": {"model_calls": budget["limits"]["model_calls"] + increment},
            },
        ).body["work"]
        assert work["continuation"]["reason"] == (
            "budget_increased" if increment else "budget_exhausted"
        )


def test_stopped_daily_never_advances_pending_occurrences(commands, account_id, urls, owner):
    work_id = accept(commands, account_id, daily=True)
    work = commands.get(account_id, work_id)["work"]
    commands.control(account_id, work_id, str(uuid4()), work["row_version"], "stop")
    schedule = owner.execute("SELECT * FROM work_schedules WHERE work_id=%s", (work_id,)).fetchone()
    due = datetime.now(UTC) - timedelta(days=1)
    occurrence = WorkScheduleService(urls[2]).occurrence(
        account_id, work_id, schedule["schedule_version"], due
    )
    assert occurrence["disposition"] == "skipped"
    assert (
        owner.execute("SELECT count(*) n FROM runs WHERE work_id=%s", (work_id,)).fetchone()["n"]
        == 0
    )


def test_same_run_stops_before_next_invocation_and_freezes_policy(
    commands, account_id, urls, owner, facts, monkeypatch
):
    from work_domain.progress import ProgressBlocked

    monkeypatch.setenv("WORK_NO_PROGRESS_LIMIT", "2")
    work_id = accept(commands, account_id)
    run = start(commands, account_id, work_id, urls, owner)
    result(facts, urls, owner, account_id, run)
    monkeypatch.setenv("WORK_NO_PROGRESS_LIMIT", "99")
    result(facts, urls, owner, account_id, run)
    with execution(urls, account_id, run) as (store, _, _), pytest.raises(ProgressBlocked):
        begin(facts, urls, store, run, str(uuid4()))
    assert commands.get(account_id, work_id)["work"]["progress"]["no_progress_limit"] == 2
    finish(urls, account_id, run)
    assert commands.get(account_id, work_id)["work"]["continuation"]["reason"] == "no_progress"


def test_infrastructure_failures_count_without_result_and_revision_resets(
    commands, account_id, urls, owner, facts
):
    work_id = accept(commands, account_id)
    for _ in range(3):
        run = start(commands, account_id, work_id, urls, owner)
        with execution(urls, account_id, run) as (store, _, _):
            begin(facts, urls, store, run, str(uuid4()))
        finish(urls, account_id, run, status="failed")
    work = commands.get(account_id, work_id)["work"]
    assert work["continuation"]["reason"] == "repeated_failure"
    revised = commands.revise(
        account_id,
        work_id,
        str(uuid4()),
        work["row_version"],
        Requirement("New strategy", "generic_work", {"schema_version": 1}),
    ).body["work"]
    assert revised["current_requirement_revision"] == 2
    assert revised["progress"] is None
    assert (
        owner.execute(
            "SELECT consecutive_failures FROM work_progress WHERE work_id=%s", (work_id,)
        ).fetchone()["consecutive_failures"]
        == 3
    )


def test_paused_producer_cannot_record_progress(commands, account_id, urls, owner, facts):
    from run_domain.execution import StaleFencingToken

    work_id = accept(commands, account_id)
    run = start(commands, account_id, work_id, urls, owner)
    with execution(urls, account_id, run) as (store, _, _):
        invocation = begin(facts, urls, store, run, str(uuid4()))
    with execution(urls, account_id, run, invocation["child_execution_id"]) as (store, _, _):
        producer = invocation["invocation_id"] + ":producer"
        store.begin_operation(producer, str(run), "result")
        work = commands.get(account_id, work_id)["work"]
        Context().run(
            commands.control, account_id, work_id, str(uuid4()), work["row_version"], "pause"
        )
        with pytest.raises(StaleFencingToken), UnitOfWork(urls[2]) as uow:
            facts.publish_result(
                uow,
                invocation_id=invocation["invocation_id"],
                producing_operation_id=producer,
                result=CapabilityResult("succeeded", "late summary"),
            )
    assert commands.get(account_id, work_id)["work"]["progress"] is None


@pytest.mark.temporal
@pytest.mark.asyncio
async def test_temporal_observation_commits_then_stops_at_guard(
    commands, account_id, urls, owner, monkeypatch
):
    import asyncio
    import os

    from support.capability_worker import activities
    from support.capability_workflow import DeterministicCapabilityWorkflow
    from temporalio.client import Client, WorkflowFailureError
    from temporalio.worker import UnsandboxedWorkflowRunner, Worker
    from test_capability_harness import tool_input

    from agent_activities.segments import SegmentActivities
    from agent_workflows.capability import CapabilityInvocationWorkflow
    from agent_workflows.contracts import RunSource, ToolExecutionResult
    from agent_workflows.tool_execution import ToolExecutionWorkflow

    queue = "progress-" + str(uuid4())
    for module in (
        "agent_workflows.tool_execution",
        "agent_workflows.capability",
        "agent_workflows.segments",
        "support.capability_workflow",
    ):
        monkeypatch.setattr(module + ".AGENT_TASK_QUEUE", queue)
    work_id = accept(commands, account_id)
    run = start(commands, account_id, work_id, urls, owner)
    probe = activities(urls[2])
    controls = SegmentActivities(probe.store)
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_NAMESPACE"]
    )
    async with Worker(
        client,
        task_queue=queue,
        workflows=[
            ToolExecutionWorkflow,
            CapabilityInvocationWorkflow,
            DeterministicCapabilityWorkflow,
        ],
        workflow_runner=UnsandboxedWorkflowRunner(),
        activities=[
            probe.prepare,
            probe.observe,
            probe.close,
            probe.publish,
            controls.acquire,
            controls.release,
            controls.begin_wait,
            controls.finish_wait,
        ],
    ):
        for index in range(3):
            tool = replace(
                tool_input(urls, account_id, run, version=index + 1),
                source=RunSource("work", str(work_id)),
            )
            pending = client.execute_workflow(
                "ToolExecutionWorkflow",
                tool,
                id="progress-" + str(uuid4()),
                task_queue=queue,
                result_type=ToolExecutionResult,
            )
            if index < 2:
                assert (await asyncio.wait_for(pending, 30)).tool_success
            else:
                with pytest.raises(WorkflowFailureError) as failed:
                    await asyncio.wait_for(pending, 30)
                error = failed.value
                types = []
                while error:
                    types.append(getattr(error, "type", None))
                    error = getattr(error, "cause", None)
                assert "no_progress" in types
    assert (
        owner.execute("SELECT version FROM agent_transcripts WHERE run_id=%s", (run,)).fetchone()[
            "version"
        ]
        == 4
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_observations WHERE run_id=%s", (run,)
        ).fetchone()["n"]
        == 3
    )
    assert (
        owner.execute(
            "SELECT count(*) n FROM work_progress_samples WHERE work_id=%s", (work_id,)
        ).fetchone()["n"]
        == 3
    )
    RunLifecycleService(urls[2]).finish(account_id, run, "failed", failure_code="no_progress")
    assert commands.get(account_id, work_id)["work"]["continuation"]["reason"] == "no_progress"


@pytest.mark.asyncio
async def test_capability_budget_headroom_is_control_failure_without_child(
    commands, account_id, urls, owner
):
    from support.capability_worker import activities
    from temporalio.exceptions import ApplicationError
    from test_capability_harness import call, tool_input

    from resources.run_budget import RunBudgetService

    work_id = accept(commands, account_id)
    run = start(commands, account_id, work_id, urls, owner)
    tool = tool_input(urls, account_id, run)
    probe = activities(urls[2])
    budget = RunBudgetService(urls[2])
    with execution(urls, account_id, run) as (store, _, token):
        hold = str(run) + ":headroom-hold"
        store.begin_operation(hold, str(run), "tool")
        budget.reserve(run, hold, {"tool_calls": 40})
        try:
            with pytest.raises(ApplicationError) as exhausted:
                await probe.prepare(call(tool, token))
            assert exhausted.value.type == "run_budget_exhausted"
            assert exhausted.value.non_retryable
        finally:
            budget.release(run, hold)
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_invocations WHERE run_id=%s", (run,)
        ).fetchone()["n"]
        == 0
    )
    RunLifecycleService(urls[2]).finish(
        account_id, run, "failed", failure_code="run_budget_exhausted"
    )
    assert commands.get(account_id, work_id)["work"]["continuation"]["reason"] == "budget_exhausted"


def test_daily_empty_success_does_not_claim_no_change(commands, account_id, urls, owner, facts):
    work_id = accept(commands, account_id, daily=True)
    for _ in range(3):
        run = start(commands, account_id, work_id, urls, owner)
        result(facts, urls, owner, account_id, run)
        finish(urls, account_id, run)
    work = commands.get(account_id, work_id)["work"]
    assert work["continuation"]["reason"] == "no_progress"
    assert work["daily_baseline"] is None


def test_valid_daily_observation_breaks_no_progress_window(
    commands, account_id, urls, owner, facts
):
    work_id = accept(commands, account_id, daily=True)
    for outcome, expected in [
        ("succeeded", 0),
        ("insufficient", 1),
        ("succeeded", 0),
        ("insufficient", 1),
        ("succeeded", 0),
    ]:
        run = start(commands, account_id, work_id, urls, owner)
        result(facts, urls, owner, account_id, run, content="a", outcome=outcome)
        finish(urls, account_id, run)
        assert (
            commands.get(account_id, work_id)["work"]["progress"]["no_progress_count"] == expected
        )


def test_budget_recovery_keeps_independent_progress_protection(
    commands, account_id, urls, owner, facts
):
    work_id = accept(commands, account_id)
    run = start(commands, account_id, work_id, urls, owner)
    for _ in range(3):
        result(facts, urls, owner, account_id, run)
    RunLifecycleService(urls[2]).finish(
        account_id, run, "failed", failure_code="run_budget_exhausted"
    )
    work = commands.get(account_id, work_id)["work"]
    assert work["continuation"]["reason"] == "budget_exhausted"
    assert work["progress"]["blocked_reason"] == "no_progress"
    budget = work["budget"]
    work = commands.integration(
        account_id,
        work_id,
        str(uuid4()),
        work["row_version"],
        "budget",
        {
            "budget_version": budget["version"],
            "limits": {"model_calls": budget["limits"]["model_calls"] + 1},
        },
    ).body["work"]
    assert work["continuation"]["reason"] == "no_progress"
    with pytest.raises(WorkConflict):
        commands.advance(account_id, work_id, str(uuid4()), work["row_version"])
