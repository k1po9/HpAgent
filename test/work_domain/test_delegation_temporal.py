"""Real Temporal fan-out plus PG persistence, with deterministic model answers."""

import asyncio
import json
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client
from temporalio.exceptions import ApplicationError
from temporalio.worker import Replayer, UnsandboxedWorkflowRunner, Worker
from test_delegation import brief

from actions.contracts import ActionRequest
from agent_activities.delegation import DelegationActivities
from agent_activities.fencing import execution_fence
from agent_activities.runtime import DurableAgentActivities
from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import AGENT_SCHEMA_VERSION, AgentRunInput, RunContext, RunSource
from agent_workflows.delegation import WorkDelegationWorkflow
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow
from application.work_context import RunExecutionBindings, WorkContextProvider
from brain.contracts import BrainDecision
from resources.run_budget import RunBudgetService
from run_domain.lifecycle import RunLifecycleService
from web_api.queries import QueryService
from work_domain.models import Requirement


class Events:
    async def progress(self, *args):
        pass

    async def close(self):
        pass

    async def model(self, **kwargs):
        pass


class EventFactory:
    def for_run(self, run_id):
        return Events()


class Resources:
    @asynccontextmanager
    async def lease_for_run(self, *args):
        yield


class Actions:
    def reset_execution(self, *args):
        pass

    def clear_execution(self, *args):
        pass

    async def select_tools(self, **kwargs):
        return []


class Brain:
    def __init__(self):
        self.entered = set()
        self.barrier = asyncio.Event()

    async def generate_chat_decision(self, *, messages, tools=None):
        user = json.loads(next(m["content"] for m in messages if m["role"] == "user"))
        if "branch_key" in user:
            key = user["branch_key"]
            self.entered.add(key)
            if len(self.entered) == 3:
                self.barrier.set()
            # This proves independent contexts really run concurrently, not serial plan steps.
            await asyncio.wait_for(self.barrier.wait(), 10)
            if key == "c":
                raise ApplicationError(
                    "direction unavailable", type="source_unavailable", non_retryable=True
                )
            return BrainDecision("Verified direction " + key, [], "stop", {})
        if not any(m["role"] == "tool" for m in messages):
            assert any(t["function"]["name"] == "delegate_work" for t in tools)
            return BrainDecision(
                "Investigate independently",
                [
                    ActionRequest(
                        "delegate", "delegate_work", {"branches": [brief(k) for k in "abc"]}
                    )
                ],
                "tool_calls",
                {},
            )
        return BrainDecision(
            '{"schema_version":1,"kind":"progress_saved","evidence":[],"continuation":'
            '{"schema_version":1,"kind":"blocked","reason":"required_branch_missing:c"}}',
            [],
            "stop",
            {},
        )

    async def generate_final_decision(self, *, messages):
        systems = " ".join(m["content"] for m in messages if m["role"] == "system")
        if '"steps"' in systems:
            return BrainDecision(
                '{"steps":[{"title":"Independent directions","objective":"Investigate in parallel"}]}',
                [],
                "stop",
                {},
            )
        if '"decision"' in systems:
            return BrainDecision(
                '{"decision":"complete","reason":"Save verified partial results"}', [], "stop", {}
            )
        return BrainDecision(
            '{"schema_version":1,"kind":"progress_saved","evidence":[],"continuation":'
            '{"schema_version":1,"kind":"blocked","reason":"required_branch_missing:c"}}',
            [],
            "stop",
            {},
        )


@pytest.mark.postgres
@pytest.mark.temporal
@pytest.mark.parametrize("strategy", ["react", "plan_and_execute"])
async def test_parallel_branches_partial_failure_and_history_replay(
    urls, commands, owner, account_id, monkeypatch, strategy
):
    if not os.getenv("TEMPORAL_HOST"):
        pytest.skip("TEMPORAL_HOST required")
    queue = "test-phase5-" + str(uuid4())
    for module in (
        "agent_workflows.agent_run",
        "agent_workflows.react",
        "agent_workflows.tool_execution",
        "agent_workflows.delegation",
        "agent_workflows.segments",
        "agent_workflows.agent_step",
        "agent_workflows.plan_execute",
    ):
        monkeypatch.setattr(module + ".AGENT_TASK_QUEUE", queue)
    accepted = commands.accept(
        account_id,
        str(uuid4()),
        "Parallel investigation",
        Requirement(
            "Investigate three independent directions",
            "generic_work",
            {"schema_version": 1, "reasoning_mode": strategy},
        ),
    )
    work = accepted.body["work"]
    run = commands.advance(
        account_id, UUID(work["work_id"]), str(uuid4()), work["row_version"]
    ).body["run"]
    RunLifecycleService(urls[2]).start(account_id, UUID(run["run_id"]))
    store = AgentDataStore(urls[2])
    root = store.run_identity(run["run_id"])["execution_id"]
    brain = Brain()
    runtime = DurableAgentActivities(
        store=store,
        loader=WorkContextProvider(urls[2]),
        brain=brain,
        actions=Actions(),
        event_factory=EventFactory(),
        resource_prep=Resources(),
        lifecycle=None,
        run_budget=RunBudgetService(urls[2]),
        context_bindings=RunExecutionBindings(),
    )
    controls = SegmentActivities(store)
    delegation = DelegationActivities(store, runtime.run_budget)
    workflows = [
        AgentRunWorkflow,
        ReactAgentWorkflow,
        PlanAndExecuteWorkflow,
        AgentStepWorkflow,
        ToolExecutionWorkflow,
        WorkDelegationWorkflow,
    ]
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )
    identity = "test-phase5-root-" + str(uuid4())
    async with Worker(
        client,
        task_queue=queue,
        workflow_runner=UnsandboxedWorkflowRunner(),
        workflows=workflows,
        activities=[
            runtime.context_bootstrap,
            runtime.model_decision,
            runtime.planning,
            runtime.evaluate_plan,
            controls.acquire,
            controls.release,
            delegation.prepare,
            delegation.finish,
            delegation.finish_branch,
        ],
    ):
        result = await client.execute_workflow(
            AgentRunWorkflow.run,
            AgentRunInput(
                schema_version=AGENT_SCHEMA_VERSION,
                run_id=run["run_id"],
                account_id=str(account_id),
                source=RunSource("work", work["work_id"]),
                context=RunContext(surface="work"),
                strategy=strategy,
                execution_id=root,
            ),
            id=identity,
            task_queue=queue,
            execution_timeout=timedelta(seconds=60),
        )
    assert brain.entered == {"a", "b", "c"}
    assert "progress_saved" in store.result_content(result.result_ref, run["run_id"])
    branches = QueryService(urls[1]).get_run(account_id, UUID(run["run_id"]))["run"]["branches"]
    assert [b["status"] for b in branches] == ["succeeded", "succeeded", "failed", "failed"]
    assert len({b["execution_id"] for b in branches}) == 4
    assert (
        owner.execute(
            "SELECT count(*) AS n FROM agent_transcripts WHERE run_id=%s", (UUID(run["run_id"]),)
        ).fetchone()["n"]
        == 5
    )
    histories = {
        ("root" if strategy == "react" else "root_plan"): await client.get_workflow_handle(
            identity
        ).fetch_history(),
        ("react" if strategy == "react" else "plan"): await client.get_workflow_handle(
            f"hpagent-agent-{strategy}-{root}"
        ).fetch_history(),
        "delegation": await client.get_workflow_handle(
            f"hpagent-delegation-{root}"
        ).fetch_history(),
        "child": await client.get_workflow_handle(
            f"hpagent-agent-react-{branches[0]['execution_id']}"
        ).fetch_history(),
    }
    strategy_history = histories["react" if strategy == "react" else "plan"]
    # The delegate tool History independently covers its controlled routing boundary.
    parent_history = strategy_history
    if strategy == "plan_and_execute":
        parent_history = await client.get_workflow_handle(
            f"hpagent-agent-step-{root}-1-step-1"
        ).fetch_history()
    tool_event = next(
        e
        for e in parent_history.events
        if e.HasField("start_child_workflow_execution_initiated_event_attributes")
    )
    tool_id = tool_event.start_child_workflow_execution_initiated_event_attributes.workflow_id
    histories["tool"] = await client.get_workflow_handle(tool_id).fetch_history()
    if strategy == "plan_and_execute":
        histories["step"] = await client.get_workflow_handle(
            f"hpagent-agent-step-{root}-1-step-1"
        ).fetch_history()
    for history in histories.values():
        await Replayer(
            workflows=workflows, workflow_runner=UnsandboxedWorkflowRunner()
        ).replay_workflow(history)
    if os.getenv("RECORD_PHASE5_HISTORY") == "1":
        directory = Path("test/fixtures/phase5")
        directory.mkdir(parents=True, exist_ok=True)
        for name, history in histories.items():
            (directory / (name + ".json")).write_text(
                history.to_json().replace(queue, "hpagent-web-agent")
            )
    assert execution_fence.get() is None
