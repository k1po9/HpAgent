from __future__ import annotations

import os
from uuid import uuid4

import pytest
from support.segment_activities import CONTROL_ACTIVITIES
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Worker

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentRunInput,
    ChatContext,
    CompactToolCall,
    ContextBootstrapInput,
    ContextBootstrapResult,
    ModelDecisionInput,
    ModelDecisionResult,
    PlanEvaluationInput,
    PlanEvaluationResult,
    PlanningInput,
    PlanningResult,
    PlanStep,
    RunContext,
    RunSource,
    ToolExecutionInput,
    ToolExecutionResult,
)
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow
from agent_workflows.tool_execution import ToolExecutionWorkflow

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]


@activity.defn(name="context_bootstrap_activity")
async def fake_context(request: ContextBootstrapInput) -> ContextBootstrapResult:
    return ContextBootstrapResult(
        AGENT_SCHEMA_VERSION,
        f"transcript:{request.run_id}",
        1,
        f"context:{request.run_id}",
    )


@activity.defn(name="model_decision_activity")
async def fake_model(request: ModelDecisionInput) -> ModelDecisionResult:
    version = request.transcript_version + 1
    if request.strategy == "react" and request.turn == 1 and not request.final_only:
        call = CompactToolCall(
            "call-1", "read_only_tool", f"decision:{request.operation_id}#call-1"
        )
        return ModelDecisionResult(
            AGENT_SCHEMA_VERSION,
            request.operation_id,
            "tool_calls",
            f"decision:{request.operation_id}",
            (call,),
            version,
            "tool_calls",
        )
    return ModelDecisionResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        "final",
        f"decision:{request.operation_id}",
        (),
        version,
        "stop",
        "done",
    )


@activity.defn(name="tool_execution_activity")
async def fake_tool(request: ToolExecutionInput) -> ToolExecutionResult:
    return ToolExecutionResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        f"tool-result:{request.operation_id}",
        request.transcript_version + 1,
        "tool done",
    )


@activity.defn(name="planning_activity")
async def fake_planning(request: PlanningInput) -> PlanningResult:
    return PlanningResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        request.plan_id,
        request.plan_version,
        (
            PlanStep("step-1", 1, "first", "first objective"),
            PlanStep("step-2", 2, "second", "second objective"),
        ),
        request.transcript_version + 1,
    )


@activity.defn(name="evaluate_plan_activity")
async def fake_evaluation(request: PlanEvaluationInput) -> PlanEvaluationResult:
    return PlanEvaluationResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        "complete" if request.step_index == request.step_count else "continue",
        "test",
    )


def _request(strategy: str) -> AgentRunInput:
    run_id = str(uuid4())
    return AgentRunInput(
        schema_version=AGENT_SCHEMA_VERSION,
        run_id=run_id,
        account_id=str(uuid4()),
        strategy=strategy,
        max_turns=3,
        source=RunSource("chat", str(uuid4())),
        context=RunContext(
            chat=ChatContext(str(uuid4()), str(uuid4()), str(uuid4())), surface="web"
        ),
    )


async def _run(strategy: str):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    client = await Client.connect(
        host, namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )
    worker = Worker(
        client,
        task_queue=AGENT_TASK_QUEUE,
        workflows=[
            AgentRunWorkflow,
            ReactAgentWorkflow,
            PlanAndExecuteWorkflow,
            AgentStepWorkflow,
            ToolExecutionWorkflow,
        ],
        activities=[*CONTROL_ACTIVITIES,
            fake_context,
            fake_model,
            fake_tool,
            fake_planning,
            fake_evaluation,
        ],
    )
    request = _request(strategy)
    async with worker:
        return await client.execute_workflow(
            AgentRunWorkflow.run,
            request,
            id=f"test-agent-run-{request.run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )


async def test_durable_react_tool_then_final():
    result = await _run("react")
    assert result.tool_turns == 1
    assert result.transcript_version == 4


async def test_durable_plan_two_steps_then_synthesis():
    result = await _run("plan_and_execute")
    assert result.tool_turns == 0
    assert result.transcript_version == 5


async def test_agent_router_rejects_unknown_strategy():
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required")
    client = await Client.connect(
        host, namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )
    worker = Worker(
        client,
        task_queue=AGENT_TASK_QUEUE,
        workflows=[AgentRunWorkflow],
    )
    request = _request("unknown")
    async with worker:
        with pytest.raises(WorkflowFailureError):
            await client.execute_workflow(
                AgentRunWorkflow.run,
                request,
                id=f"test-agent-run-{request.run_id}",
                task_queue=AGENT_TASK_QUEUE,
            )


class ReplanActivities:
    def __init__(self, continuous):
        self.continuous = continuous
        self.plans = []

    @activity.defn(name="planning_activity")
    async def planning(self, request: PlanningInput) -> PlanningResult:
        self.plans.append(request)
        if request.plan_version > 1:
            assert request.previous_plan_version == request.plan_version - 1
            assert request.previous_plan_ref.endswith(f":v{request.previous_plan_version}")
            assert len(request.completed_step_refs) == request.plan_version - 1
            assert request.evaluation_reason == "new evidence"
        return PlanningResult(
            AGENT_SCHEMA_VERSION, request.operation_id, request.plan_id, request.plan_version,
            (PlanStep(f"step-v{request.plan_version}", 1, "next", "next objective"),),
            request.transcript_version + 1,
        )

    @activity.defn(name="evaluate_plan_activity")
    async def evaluate(self, request: PlanEvaluationInput) -> PlanEvaluationResult:
        return PlanEvaluationResult(
            AGENT_SCHEMA_VERSION, request.operation_id,
            "replan" if self.continuous or request.plan_version == 1 else "complete", "new evidence",
        )


@pytest.mark.parametrize("continuous", [False, True])
async def test_replan_carries_completed_refs_and_has_a_finite_limit(continuous):
    from temporalio.worker import Replayer

    if not os.getenv("TEMPORAL_HOST"):
        pytest.skip("TEMPORAL_HOST required")
    client = await Client.connect(os.environ["TEMPORAL_HOST"], namespace=os.getenv("TEMPORAL_NAMESPACE", "default"))
    activities = ReplanActivities(continuous)
    request = _request("plan_and_execute")
    async with Worker(client, task_queue=AGENT_TASK_QUEUE,
                      workflows=[PlanAndExecuteWorkflow, AgentStepWorkflow, ToolExecutionWorkflow],
                      activities=[*CONTROL_ACTIVITIES, fake_context, fake_model, fake_tool,
                                  activities.planning, activities.evaluate]):
        handle = await client.start_workflow(PlanAndExecuteWorkflow.run, request,
                                             id=f"replan-{request.run_id}", task_queue=AGENT_TASK_QUEUE)
        if continuous:
            with pytest.raises(WorkflowFailureError) as failure:
                await handle.result()
            assert failure.value.cause.type == "plan_replan_limit_reached"
            assert [item.plan_version for item in activities.plans] == [1, 2, 3, 4]
        else:
            result = await handle.result()
            assert result.result_ref.endswith(":plan:2:synthesis")
            assert [item.plan_version for item in activities.plans] == [1, 2]
        assert len({item.operation_id for item in activities.plans}) == len(activities.plans)
        await Replayer(workflows=[PlanAndExecuteWorkflow]).replay_workflow(await handle.fetch_history())
