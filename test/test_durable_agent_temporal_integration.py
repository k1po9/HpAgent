from __future__ import annotations

import os
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Worker

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    AgentRunInput,
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
    ToolExecutionInput,
    ToolExecutionResult,
)
from agent_workflows.plan_execute import PlanAndExecuteWorkflow
from agent_workflows.react import ReactAgentWorkflow

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
        AGENT_SCHEMA_VERSION,
        run_id,
        str(uuid4()),
        str(uuid4()),
        str(uuid4()),
        strategy,
        str(uuid4()),
        1,
        "web_plan" if strategy == "plan_and_execute" else "web_chat",
        3,
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
        ],
        activities=[
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
