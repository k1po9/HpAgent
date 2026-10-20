"""S2 transactional and real Temporal recovery gates, using explicit isolated services."""

import asyncio
import os
import subprocess
import sys
from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from support.capability_worker import activities
from temporalio.client import Client
from test_capability_facts import chat, execution

from agent_workflows.capability_contracts import CapabilityCall, CapabilityClose, CapabilityFinish
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    CompactToolCall,
    RunContext,
    RunSource,
    ToolExecutionInput,
    ToolExecutionResult,
)
from persistence.uow import UnitOfWork
from run_domain.execution import TranscriptVersionConflict
from run_domain.results import complete_operation

pytestmark = pytest.mark.postgres


def tool_input(urls, account_id, run_id, text="hello", version=1):
    with execution(urls, account_id, run_id) as (store, segment, _):
        suffix = str(uuid4())
        transcript = f"t:{run_id}"
        if version == 1:
            store.begin_operation(f"context:{run_id}", str(run_id), "context")
            store.create_transcript(
                transcript_id=transcript,
                run_id=str(run_id),
                account_id=str(account_id),
                execution_id=segment.execution_id,
                conversation_id=store.run_identity(str(run_id))["conversation_id"],
                messages=[{"role": "user", "content": "test"}],
                operation_id=f"context:{run_id}",
            )
        decision = f"decision:{suffix}"
        name = next(iter(activities(urls[2]).bindings))
        store.begin_operation(decision, str(run_id), "model")
        with UnitOfWork(urls[2]) as uow:
            complete_operation(
                uow, decision, decision, {"tool_call_arguments": {suffix: {"text": text}}}
            )
        return ToolExecutionInput(
            AGENT_SCHEMA_VERSION,
            str(run_id),
            str(account_id),
            RunSource("chat", str(run_id)),
            RunContext(),
            "react",
            transcript,
            version,
            0,
            f"call:{suffix}",
            0,
            CompactToolCall(suffix, name, f"{decision}#{suffix}"),
            execution_id=segment.execution_id,
        )


def call(tool, token=0):
    return CapabilityCall(
        tool, tool.account_id, tool.run_id, tool.execution_id, tool.operation_id, token
    )


def finish(tool, token):
    return CapabilityFinish(
        call(tool), None, tool.account_id, tool.run_id, tool.execution_id, tool.operation_id, token
    )


async def test_observation_conflict_rolls_back_and_response_loss_replays(urls, account_id, owner):
    run = chat(urls, account_id)
    tool = tool_input(urls, account_id, run)
    conflicting = replace(
        tool_input(urls, account_id, run, text="changed"), operation_id=tool.operation_id
    )
    probe = activities(urls[2])
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await probe.prepare(call(tool, token))
        again = await probe.prepare(call(tool, token))
        from temporalio.exceptions import ApplicationError

        with pytest.raises(ApplicationError) as conflict:
            await probe.prepare(call(conflicting, token))
        assert conflict.value.type == "capability_identity_conflict"
        assert prepared.error is None
        assert again.child == prepared.child
    with execution(urls, account_id, run, prepared.child.execution_id) as (_, _, token):
        await probe.publish(replace(prepared.child, lease_token=token))
    await probe.close(CapabilityClose(prepared.child, "succeeded"))
    with execution(urls, account_id, run) as (_, _, token):
        with pytest.raises(TranscriptVersionConflict):
            await probe.observe(finish(replace(tool, transcript_version=99), token))
        assert (
            owner.execute(
                "SELECT count(*) n FROM capability_observations WHERE invocation_id=%s",
                (tool.operation_id,),
            ).fetchone()["n"]
            == 0
        )
        observed = await probe.observe(finish(tool, token))
        assert observed.tool_success
        assert await probe.observe(finish(tool, token)) == observed
        assert (await probe.prepare(call(tool, token))).replay == observed
    assert (
        owner.execute(
            "SELECT version FROM agent_transcripts WHERE transcript_id=%s", (tool.transcript_id,)
        ).fetchone()["version"]
        == 2
    )


@pytest.mark.temporal
async def test_worker_kill_after_result_then_two_calls_without_duplicate_usage(
    urls, account_id, owner
):
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_NAMESPACE"]
    )
    run = chat(urls, account_id)
    tool = tool_input(urls, account_id, run)
    worker_log = open("/tmp/hpagent-s2-capability-worker.log", "a")

    def worker():
        return subprocess.Popen(
            [sys.executable, "-m", "support.capability_worker"],
            stdout=worker_log,
            stderr=worker_log,
        )

    process = worker()
    try:
        handle = await client.start_workflow(
            "ToolExecutionWorkflow",
            tool,
            id=f"s2-kill-{uuid4()}",
            task_queue="hpagent-web-agent",
            result_type=ToolExecutionResult,
        )

        async def committed():
            for _ in range(400):
                row = owner.execute(
                    "SELECT 1 FROM capability_results WHERE invocation_id=%s", (tool.operation_id,)
                ).fetchone()
                if row:
                    return
                if process.poll() is not None:
                    pytest.fail("isolated test Worker exited before commit")
                await asyncio.sleep(0.1)
            pytest.fail("capability result commit deadline exceeded")

        await committed()
        # Producer committed; parent is waiting without a lease or physical capacity.
        assert not owner.execute(
            "SELECT 1 FROM execution_attempt_leases WHERE execution_id=%s "
            "AND lease_expires_at>clock_timestamp()",
            (UUID(tool.execution_id),),
        ).fetchone()
        process.kill()
        await asyncio.to_thread(process.wait)
        process = worker()
        result = await asyncio.wait_for(handle.result(), 60)
        assert result.tool_success
        second = tool_input(urls, account_id, run, text="second", version=result.transcript_version)
        result2 = await asyncio.wait_for(
            client.execute_workflow(
                "ToolExecutionWorkflow",
                second,
                id=f"s2-second-{uuid4()}",
                task_queue="hpagent-web-agent",
                result_type=ToolExecutionResult,
            ),
            60,
        )
        assert result2.transcript_version == 3
        rows = owner.execute("SELECT * FROM capability_results WHERE run_id=%s", (run,)).fetchall()
        assert len(rows) == 2 and {r["payload"]["summary"] for r in rows} == {"hello", "second"}
        usage = owner.execute(
            "SELECT sum(actual_amount) n FROM run_usage_ledger WHERE run_id=%s "
            "AND dimension='tool_calls' AND state='settled'",
            (run,),
        ).fetchone()
        assert usage["n"] == 2
        assert (
            owner.execute(
                "SELECT count(*) n FROM capability_observations WHERE run_id=%s", (run,)
            ).fetchone()["n"]
            == 2
        )
        third = tool_input(urls, account_id, run, text="cancel", version=result2.transcript_version)
        cancelled = await client.start_workflow(
            "ToolExecutionWorkflow",
            third,
            id=f"s2-cancel-{uuid4()}",
            task_queue="hpagent-web-agent",
            result_type=ToolExecutionResult,
        )
        for _ in range(400):
            if owner.execute(
                "SELECT 1 FROM capability_results WHERE invocation_id=%s", (third.operation_id,)
            ).fetchone():
                break
            await asyncio.sleep(0.1)
        else:
            pytest.fail("cancel test result commit deadline exceeded")
        await cancelled.cancel()
        from temporalio.client import WorkflowFailureError

        with pytest.raises(WorkflowFailureError):
            await asyncio.wait_for(cancelled.result(), 30)
        assert (
            owner.execute(
                "SELECT status FROM run_executions WHERE invocation_id=%s", (third.operation_id,)
            ).fetchone()["status"]
            == "cancelled"
        )
        assert not owner.execute(
            "SELECT 1 FROM capability_observations WHERE invocation_id=%s", (third.operation_id,)
        ).fetchone()
    finally:
        process.terminate()
        await asyncio.to_thread(process.wait, 15)
        worker_log.close()


async def test_admission_capacity_and_persistent_call_limits(urls, account_id, owner):

    from resources.run_budget import CapabilityBudgetExhausted, RunBudgetService

    run = chat(urls, account_id)
    first = tool_input(urls, account_id, run)
    second = tool_input(urls, account_id, run)
    probe = activities(urls[2])
    assert probe.discover("chat")[0]["function"]["name"] == first.tool_call.name
    assert probe.discover("unsupported") == []
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await probe.prepare(call(first, token))
        denied = await probe.prepare(call(second, token))
        assert denied.child is None and "capacity" in denied.error
        response = await probe.observe(replace(finish(second, token), error=denied.error))
        assert response.tool_success is False
    with execution(urls, account_id, run, prepared.child.execution_id) as (_, _, token):
        budget = RunBudgetService(urls[2])
        for number in range(2):
            mutation = budget.reserve(run, f"{first.operation_id}:tool:{number}", {"tool_calls": 1})
            assert not mutation.replayed
            assert budget.reserve(
                run, f"{first.operation_id}:tool:{number}", {"tool_calls": 1}
            ).replayed
            budget.settle(run, f"{first.operation_id}:tool:{number}", {"tool_calls": 1}, "measured")
        with pytest.raises(CapabilityBudgetExhausted, match="invocation call limit"):
            budget.reserve(run, f"{first.operation_id}:tool:excess", {"tool_calls": 1})
        with pytest.raises(CapabilityBudgetExhausted, match="invocation call limit"):
            budget.reserve(run, f"{first.operation_id}:model:excess", {"model_calls": 1})
    assert not owner.execute(
        "SELECT 1 FROM run_usage_ledger WHERE operation_id=%s",
        (f"{first.operation_id}:tool:excess",),
    ).fetchone()


@pytest.mark.parametrize("action", ["pause", "revise", "stop"])
async def test_parent_control_after_producer_commit_prevents_observation(
    urls, commands, account_id, owner, action
):
    from contextvars import Context

    from temporalio.exceptions import ApplicationError

    from work_domain.models import Requirement

    requirement = Requirement("Test capability", "generic_work", {"schema_version": 1})
    accepted = commands.accept(account_id, str(uuid4()), "Test", requirement)
    work_id = UUID(accepted.body["work"]["work_id"])
    admitted = commands.advance(account_id, work_id, str(uuid4()), 1)
    run = UUID(admitted.body["run"]["run_id"])
    tool = tool_input(urls, account_id, run)
    probe = activities(urls[2])
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await probe.prepare(call(tool, token))
    with execution(urls, account_id, run, prepared.child.execution_id) as (_, _, child_token):
        await probe.publish(replace(prepared.child, lease_token=child_token))
    with execution(urls, account_id, run) as (_, _, token):
        current = commands.get(account_id, work_id)["work"]
        if action == "revise":
            Context().run(
                commands.revise,
                account_id,
                work_id,
                str(uuid4()),
                current["row_version"],
                requirement,
            )
        else:
            Context().run(
                commands.control, account_id, work_id, str(uuid4()), current["row_version"], action
            )
        with pytest.raises(ApplicationError) as exc:
            await probe.observe(finish(tool, token))
        assert exc.value.type == "stale_fencing_token"
    assert owner.execute(
        "SELECT 1 FROM capability_results WHERE invocation_id=%s", (tool.operation_id,)
    ).fetchone()
    assert not owner.execute(
        "SELECT 1 FROM capability_observations WHERE invocation_id=%s", (tool.operation_id,)
    ).fetchone()


async def test_old_producer_attempt_retains_receipt_without_publishing(urls, account_id, owner):
    from run_domain.execution_context import fence_scope

    run = chat(urls, account_id)
    tool = tool_input(urls, account_id, run)
    probe = activities(urls[2])
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await probe.prepare(call(tool, token))
    child = prepared.child
    with execution(urls, account_id, run, child.execution_id) as (store, _, token):
        store.begin_operation(child.operation_id, str(run), "tool")
        old = replace(child, lease_token=token)
    with execution(urls, account_id, run, child.execution_id) as (store, _, token):
        store.begin_operation(child.operation_id, str(run), "tool")
        from run_domain.capability_contracts import CapabilityResult
        from run_domain.execution import StaleFencingToken

        with fence_scope(old.account_id, old.run_id, old.lease_token, old.execution_id):
            with pytest.raises(StaleFencingToken):
                probe.commit_result(old, CapabilityResult("succeeded", "late"))
            from resources.run_budget import RunBudgetService

            with pytest.raises(StaleFencingToken):
                RunBudgetService(urls[2]).reserve(
                    run, old.operation_id + ":late-budget", {"tool_calls": 1}
                )
        assert not owner.execute(
            "SELECT 1 FROM capability_results WHERE invocation_id=%s", (tool.operation_id,)
        ).fetchone()
        receipts = owner.execute(
            "SELECT disposition FROM execution_result_receipts WHERE operation_id=%s",
            (old.operation_id,),
        ).fetchall()
        assert any(row["disposition"] != "current" for row in receipts)


@pytest.mark.temporal
async def test_shared_react_harness_discovers_capability_and_observes_two_results(
    urls, commands, account_id, owner
):
    from support.capability_workflow import DeterministicCapabilityWorkflow
    from temporalio.worker import Replayer, Worker
    from test_delegation_temporal import Actions, EventFactory, Resources

    from actions.contracts import ActionRequest
    from agent_activities.runtime import DurableAgentActivities
    from agent_activities.segments import SegmentActivities
    from agent_workflows.agent_run import AgentRunWorkflow
    from agent_workflows.capability import CapabilityInvocationWorkflow
    from agent_workflows.contracts import AgentRunInput
    from agent_workflows.react import ReactAgentWorkflow
    from agent_workflows.tool_execution import ToolExecutionWorkflow
    from application.work_context import RunExecutionBindings, WorkContextProvider
    from brain.contracts import BrainDecision
    from resources.run_budget import RunBudgetService
    from run_domain.lifecycle import RunLifecycleService
    from work_domain.models import Requirement

    class Brain:
        async def generate_chat_decision(self, *, messages, tools=None):
            observations = [m for m in messages if m["role"] == "tool"]
            if len(observations) < 2:
                available = [t for t in tools if t["function"]["name"].startswith("capability__")]
                assert len(available) == 1
                return BrainDecision(
                    "invoke",
                    [
                        ActionRequest(
                            f"echo-{len(observations)}",
                            available[0]["function"]["name"],
                            {"text": f"echo-{len(observations)}"},
                        )
                    ],
                    "tool_calls",
                    {},
                )
            assert "echo-0" in observations[0]["content"] and "echo-1" in observations[1]["content"]
            return BrainDecision("Observed both committed results", [], "stop", {})

    work = commands.accept(
        account_id,
        str(uuid4()),
        "Harness",
        Requirement(
            "Test composition",
            "generic_work",
            {"schema_version": 1},
        ),
    ).body["work"]
    run = commands.advance(account_id, UUID(work["work_id"]), str(uuid4()), 1).body["run"]
    RunLifecycleService(urls[2]).start(account_id, UUID(run["run_id"]))
    probe = activities(urls[2])
    store = probe.store
    root = store.run_identity(run["run_id"])["execution_id"]
    runtime = DurableAgentActivities(
        store=store,
        loader=WorkContextProvider(urls[2]),
        brain=Brain(),
        actions=Actions(),
        event_factory=EventFactory(),
        resource_prep=Resources(),
        lifecycle=None,
        run_budget=RunBudgetService(urls[2]),
        context_bindings=RunExecutionBindings(),
        capabilities=probe,
    )
    controls = SegmentActivities(store)
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.environ["TEMPORAL_NAMESPACE"]
    )
    workflows = [
        AgentRunWorkflow,
        ReactAgentWorkflow,
        ToolExecutionWorkflow,
        CapabilityInvocationWorkflow,
        DeterministicCapabilityWorkflow,
    ]
    async with Worker(
        client,
        task_queue="hpagent-web-agent",
        workflows=workflows,
        activities=[
            runtime.context_bootstrap,
            runtime.model_decision,
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
        handle = await client.start_workflow(
            AgentRunWorkflow.run,
            AgentRunInput(
                schema_version=AGENT_SCHEMA_VERSION,
                run_id=run["run_id"],
                account_id=str(account_id),
                source=RunSource("work", work["work_id"]),
                context=RunContext(surface="work"),
                strategy="react",
                execution_id=root,
            ),
            id=f"s2-harness-{uuid4()}",
            task_queue="hpagent-web-agent",
        )
        result = await asyncio.wait_for(handle.result(), 60)
        history = await handle.fetch_history()
    await Replayer(workflows=workflows).replay_workflow(history)
    assert "Observed both" in store.result_content(result.result_ref, run["run_id"])
    assert (
        owner.execute(
            "SELECT count(*) n FROM capability_observations WHERE run_id=%s", (UUID(run["run_id"]),)
        ).fetchone()["n"]
        == 2
    )
    # Returning from the Harness is not a top-level Run/Work completion command.
    assert (
        owner.execute("SELECT status FROM runs WHERE run_id=%s", (UUID(run["run_id"]),)).fetchone()[
            "status"
        ]
        == "running"
    )


async def test_uncertain_result_is_control_state_not_parent_observation(urls, account_id, owner):
    from temporalio.exceptions import ApplicationError

    from run_domain.capability_contracts import CapabilityResult

    run = chat(urls, account_id)
    tool = tool_input(urls, account_id, run)
    probe = activities(urls[2])
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await probe.prepare(call(tool, token))
    with execution(urls, account_id, run, prepared.child.execution_id) as (store, _, token):
        child = replace(prepared.child, lease_token=token)
        store.begin_operation(child.operation_id, str(run), "tool")
        probe.commit_result(child, CapabilityResult("uncertain", "Needs reconciliation"))
    await probe.close(CapabilityClose(prepared.child, "succeeded"))
    with execution(urls, account_id, run) as (_, _, token):
        assert (await probe.prepare(call(tool, token))).result_committed
        with pytest.raises(ApplicationError) as exc:
            await probe.observe(finish(tool, token))
        assert exc.value.type == "capability_uncertain"
    assert not owner.execute(
        "SELECT 1 FROM capability_observations WHERE invocation_id=%s", (tool.operation_id,)
    ).fetchone()


async def test_input_revocation_blocks_result_summary_delivery(urls, account_id, owner):
    from test_capability_contracts import scope
    from test_capability_facts import output_file

    from agent_activities.capabilities import CapabilityBinding
    from workspace.resources import ResourceDenied

    source_run = chat(urls, account_id)
    file_id = output_file(owner, account_id, source_run)
    run = chat(urls, account_id)
    tool = tool_input(urls, account_id, run)
    cid = owner.execute("SELECT conversation_id FROM runs WHERE run_id=%s", (run,)).fetchone()[
        "conversation_id"
    ]
    owner.execute(
        "INSERT INTO conversation_resource_files(account_id,conversation_id,file_id) VALUES (%s,%s,%s)",
        (account_id, cid, file_id),
    )
    owner.execute(
        "INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,basis) VALUES (%s,%s,%s,%s,'explicit_attachment')",
        (uuid4(), account_id, run, file_id),
    )
    probe = activities(urls[2])
    binding = CapabilityBinding(
        "test.echo",
        1,
        "DeterministicCapabilityWorkflow",
        lambda inputs: scope(file_ids=[str(file_id)]),
    )
    probe.bindings = {binding.tool_name: binding}
    with execution(urls, account_id, run) as (_, _, token):
        prepared = await probe.prepare(call(tool, token))
        assert prepared.error is None
    with execution(urls, account_id, run, prepared.child.execution_id) as (_, _, token):
        await probe.publish(replace(prepared.child, lease_token=token))
    await probe.close(CapabilityClose(prepared.child, "succeeded"))
    owner.execute(
        "UPDATE conversation_resource_files SET available=false WHERE account_id=%s AND conversation_id=%s AND file_id=%s",
        (account_id, cid, file_id),
    )
    with execution(urls, account_id, run) as (_, _, token):
        with pytest.raises(ResourceDenied, match="revoked"):
            await probe.observe(finish(tool, token))
    assert not owner.execute(
        "SELECT 1 FROM capability_observations WHERE invocation_id=%s", (tool.operation_id,)
    ).fetchone()
