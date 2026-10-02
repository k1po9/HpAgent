"""Real role boundaries for bounded delegation, isolation, retries and revision fences."""

import json
from dataclasses import replace
from uuid import UUID, uuid4

import psycopg
import pytest

from agent_activities.delegation import DelegationActivities, DelegationDenied, validate_briefs
from agent_activities.fencing import fence_scope
from agent_activities.store import AgentDataStore, StaleFencingToken
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    CompactToolCall,
    RunContext,
    RunSource,
    ToolExecutionInput,
)
from agent_workflows.delegation_contracts import BranchFinishInput, DelegationInput
from agent_workflows.lifecycle_contracts import SegmentInput
from application.work_context import WorkContextProvider, generic_result
from persistence.uow import UnitOfWork
from resources.run_budget import RunBudgetService
from run_domain.lifecycle import RunLifecycleService
from web_api.queries import QueryService
from work_domain.models import Requirement
from workspace.catalog import WorkspaceCatalog


def brief(key="a", required=True):
    return {
        "branch_key": key,
        "objective": "Investigate direction " + key,
        "constraints": ["Report source gaps"],
        "output_contract": "Summary and evidence references",
        "node_ids": [],
        "file_ids": [],
        "tool_names": [],
        "required": required,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        lambda b: b * 4,
        lambda b: [b[0], b[0]],
        lambda b: [dict(b[0], tool_names=["delegate_work"])],
        lambda b: [dict(b[0], objective="x" * 2001)],
        lambda b: [dict(b[0], required="false")],
    ],
)
def test_invalid_briefs_fail_closed(mutation):
    with pytest.raises(ValueError):
        validate_briefs({"branches": mutation([brief()])})


def setup(commands, account_id, urls):
    accepted = commands.accept(
        account_id,
        str(uuid4()),
        "Parallel investigation",
        Requirement(
            "Investigate three directions",
            "generic_work",
            {"schema_version": 1, "reasoning_mode": "react"},
        ),
    )
    work = accepted.body["work"]
    run = commands.advance(
        account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"]
    ).body["run"]
    RunLifecycleService(urls[2]).start(account_id, UUID(run["run_id"]))
    store = AgentDataStore(urls[2])
    root = store.run_identity(run["run_id"])["execution_id"]
    seg = SegmentInput(2, run["run_id"], str(account_id), str(uuid4()), execution_id=root)
    token = store.acquire_segment(seg)
    return work, run, store, root, seg, token


def request(store, account_id, run, root, briefs):
    decision = root + ":decision"
    reference = "agent-decision:" + decision
    store.begin_operation(decision, run["run_id"], "model")
    store.complete_operation(
        decision,
        reference,
        {
            "content": "delegate",
            "delegatable_tools": [],
            "tool_call_arguments": {"call": {"branches": briefs}},
        },
    )
    store.begin_tool_operation(root + ":delegate", run["run_id"])
    tool = ToolExecutionInput(
        AGENT_SCHEMA_VERSION,
        run["run_id"],
        str(account_id),
        RunSource("work", run["work_id"]),
        RunContext(surface="work"),
        "react",
        "unused",
        1,
        1,
        root + ":delegate",
        0,
        CompactToolCall("call", "delegate_work", reference + "#call"),
        execution_id=root,
    )
    return DelegationInput(
        tool,
        execution_id=root,
        account_id=str(account_id),
        run_id=run["run_id"],
        operation_id=tool.operation_id,
    )


@pytest.mark.postgres
async def test_three_branches_are_isolated_and_only_failed_branch_retries(
    owner, urls, commands, account_id, tmp_path, monkeypatch
):
    work, run, store, root, root_seg, token = setup(commands, account_id, urls)
    activities = DelegationActivities(store, RunBudgetService(urls[2]))
    with fence_scope(str(account_id), run["run_id"], token, root):
        req = request(store, account_id, run, root, [brief(k) for k in "abc"])
        with pytest.raises(DelegationDenied):
            activities._prepare(req, [dict(brief(), tool_names=["create_work"])])
        with pytest.raises(DelegationDenied):
            activities._prepare(req, [dict(brief(), file_ids=[str(uuid4())])])
        with monkeypatch.context() as bounded_capacity:
            bounded_capacity.setenv("WORK_ACCOUNT_SUBAGENTS", "0")
            with pytest.raises(DelegationDenied, match="capacity"):
                activities._prepare(req, [brief(k) for k in "abc"])
        original_limits = owner.execute(
            "SELECT limits FROM work_budgets WHERE work_id=%s", (UUID(work["work_id"]),)
        ).fetchone()["limits"]
        owner.execute(
            "UPDATE work_budgets SET limits=jsonb_set(limits,'{model_calls}','0') WHERE work_id=%s",
            (UUID(work["work_id"]),),
        )
        with pytest.raises(DelegationDenied, match="budget"):
            activities._prepare(req, [brief(k) for k in "abc"])
        owner.execute(
            "UPDATE work_budgets SET limits=%s WHERE work_id=%s",
            (psycopg.types.json.Jsonb(original_limits), UUID(work["work_id"])),
        )
        children = activities._prepare(req, [brief(k) for k in "abc"])
        assert children == activities._prepare(req, [brief(k) for k in "abc"])
    store.release_segment(root_seg)
    transcripts = []
    for index, child in enumerate(children):
        seg = SegmentInput(
            2, child.run_id, child.account_id, str(uuid4()), execution_id=child.execution_id
        )
        lease = store.acquire_segment(seg)
        op = child.execution_id + ":result"
        with fence_scope(child.account_id, child.run_id, lease, child.execution_id):
            loaded = await WorkContextProvider(urls[2]).load(child.run_id)
            assert loaded.execution_id == child.execution_id
            assert "checkpoint" not in str(loaded.context)
            store.begin_operation(op + ":context", child.run_id, "context")
            transcript = "t:" + child.execution_id
            store.create_transcript(
                transcript_id=transcript,
                run_id=child.run_id,
                account_id=child.account_id,
                execution_id=child.execution_id,
                messages=[{"role": "user", "content": brief("abc"[index])["objective"]}],
                operation_id=op + ":context",
            )
            store.begin_operation(op, child.run_id, "model")
            from storage.tenant_file_store import TenantFileStore
            from workspace.file_scope import RunFileWorkspace

            files = RunFileWorkspace(
                urls[2],
                TenantFileStore(tmp_path / "files", max_bytes=1024),
                tmp_path / "executions",
            )
            with files.prepare(account_id, UUID(child.run_id)) as scope:
                assert child.execution_id in str(scope.scratch_root)
                assert root not in str(scope.scratch_root)
                assert not list(scope.scratch_root.iterdir())
            for sibling in transcripts:
                with pytest.raises(StaleFencingToken):
                    store.load_messages(sibling)
            with pytest.raises(DelegationDenied):
                WorkspaceCatalog(urls[2]).initialize(account_id)
            with pytest.raises(DelegationDenied):
                activities._prepare(
                    replace(req, execution_id=child.execution_id), [brief("nested")]
                )
            RunBudgetService(urls[2]).reserve(UUID(child.run_id), op + ":cost", {"tool_calls": 1})
            RunBudgetService(urls[2]).settle(
                UUID(child.run_id), op + ":cost", {"tool_calls": 1}, "measured"
            )
            if index < 2:
                store.complete_operation(op, "result:" + op, {"content": "Direction verified"})
        transcripts.append(transcript)
        store.release_segment(seg)
        activities._finish_branch(
            BranchFinishInput(
                AGENT_SCHEMA_VERSION,
                child.execution_id,
                child.run_id,
                child.account_id,
                "succeeded" if index < 2 else "failed",
                "result:" + op if index < 2 else None,
                "source_unavailable" if index == 2 else None,
            )
        )
    root_seg = replace(root_seg, segment_id=str(uuid4()))
    token = store.acquire_segment(root_seg)
    with fence_scope(str(account_id), run["run_id"], token, root):
        retry = activities._prepare(replace(req, branch_attempt=2), [brief(k) for k in "abc"])
        assert len(retry) == 1 and retry[0].execution_id not in [c.execution_id for c in children]
        activities._finish_branch(
            BranchFinishInput(
                AGENT_SCHEMA_VERSION,
                retry[0].execution_id,
                run["run_id"],
                str(account_id),
                "failed",
                error_code="source_unavailable",
            )
        )
        assert json.loads(json.dumps(activities._aggregate(req)))["success"] is False
        from work_domain.persistence import WorkRepository

        with UnitOfWork(urls[2]) as uow:
            fixed_run = dict(
                uow.execute("SELECT * FROM runs WHERE run_id=%s", (UUID(run["run_id"]),)).fetchone()
            )
            fixed_work = WorkRepository.get(uow, account_id, UUID(work["work_id"]))
            result, completion = generic_result(
                uow,
                fixed_run,
                fixed_work,
                '{"schema_version":1,"kind":"deliverable_ready","evidence":[]}',
                "root-summary",
            )
            assert not completion and result["kind"] == "progress_saved"
    store.release_segment(root_seg)
    snapshot = QueryService(urls[1]).get_run(account_id, UUID(run["run_id"]))
    assert len(snapshot["run"]["branches"]) == 4
    assert (
        sum(r["usage"].get("tool_calls", {}).get("used", 0) for r in snapshot["run"]["branches"])
        == 3
    )
    assert (
        commands.get(account_id, UUID(work["work_id"]))["work"]["budget"]["used"]["tool_calls"] == 3
    )
    # A child parent and a cross-account parent are both rejected by real schema constraints.
    original = owner.execute(
        "SELECT * FROM run_executions WHERE execution_id=%s", (UUID(children[0].execution_id),)
    ).fetchone()
    with pytest.raises(psycopg.Error):
        owner.execute(
            "INSERT INTO run_executions(execution_id,account_id,run_id,role,parent_execution_id,branch_key,context_manifest,resource_scope) "
            "VALUES (%s,%s,%s,'subagent',%s,'nested',%s,%s)",
            (
                uuid4(),
                account_id,
                UUID(run["run_id"]),
                UUID(children[0].execution_id),
                psycopg.types.json.Jsonb(original["context_manifest"]),
                psycopg.types.json.Jsonb(original["resource_scope"]),
            ),
        )


@pytest.mark.postgres
def test_revision_fences_all_children_and_retains_late_receipt(owner, urls, commands, account_id):
    work, run, store, root, seg, token = setup(commands, account_id, urls)
    activities = DelegationActivities(store, RunBudgetService(urls[2]))
    with fence_scope(str(account_id), run["run_id"], token, root):
        req = request(store, account_id, run, root, [brief()])
        child = activities._prepare(req, [brief()])[0]
    store.release_segment(seg)
    seg = replace(seg, segment_id=str(uuid4()), execution_id=child.execution_id)
    token = store.acquire_segment(seg)
    op = child.execution_id + ":late"
    with fence_scope(str(account_id), run["run_id"], token, child.execution_id):
        store.begin_operation(op, run["run_id"], "model")
    current = commands.get(account_id, UUID(work["work_id"]))["work"]
    commands.revise(
        account_id,
        UUID(work["work_id"]),
        str(uuid4()),
        current["row_version"],
        Requirement(
            "New investigation", "generic_work", {"schema_version": 1, "reasoning_mode": "react"}
        ),
    )
    with fence_scope(str(account_id), run["run_id"], token, child.execution_id):
        with pytest.raises(StaleFencingToken):
            store.complete_operation(op, "late-result", {"content": "Late evidence"})
    receipt = owner.execute(
        "SELECT * FROM execution_result_receipts WHERE operation_id=%s", (op,)
    ).fetchone()
    assert receipt["disposition"] == "stale" and receipt["requirement_revision"] == 1
    assert (
        owner.execute(
            "SELECT status FROM run_executions WHERE execution_id=%s", (UUID(child.execution_id),)
        ).fetchone()["status"]
        == "cancelling"
    )
    assert (
        commands.get(account_id, UUID(work["work_id"]))["work"]["current_requirement_revision"] == 2
    )
    store.release_segment(seg)
