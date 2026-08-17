"""Subprocess worker used by the real Temporal worker-restart acceptance tests."""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from pathlib import Path

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.agent_step import AgentStepWorkflow
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
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

_STATE_DIR: Path


def _record(operation_id: str, kind: str, plan_version: int | None = None) -> bool:
    with sqlite3.connect(_STATE_DIR / "operations.sqlite") as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS attempts (operation_id TEXT NOT NULL, kind TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS operations ("
            "operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, plan_version INTEGER)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS side_effects (operation_id TEXT PRIMARY KEY)"
        )
        connection.execute(
            "INSERT INTO attempts(operation_id, kind) VALUES (?, ?)",
            (operation_id, kind),
        )
        cursor = connection.execute(
            "INSERT OR IGNORE INTO operations(operation_id, kind, plan_version) VALUES (?, ?, ?)",
            (operation_id, kind, plan_version),
        )
        connection.commit()
        return cursor.rowcount == 1


async def _wait_for_release(name: str) -> None:
    (_STATE_DIR / f"{name}.boundary").touch()
    while not (_STATE_DIR / f"{name}.release").exists():
        activity.heartbeat({"boundary": name})
        await asyncio.sleep(0.05)


@activity.defn(name="context_bootstrap_activity")
async def context_activity(request: ContextBootstrapInput) -> ContextBootstrapResult:
    _record(request.operation_id, "context")
    return ContextBootstrapResult(
        AGENT_SCHEMA_VERSION,
        f"transcript:{request.run_id}",
        1,
        f"context:{request.run_id}",
    )


@activity.defn(name="model_decision_activity")
async def model_activity(request: ModelDecisionInput) -> ModelDecisionResult:
    _record(request.operation_id, "model", request.plan_version)
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
            request.transcript_version + 1,
            "tool_calls",
        )
    if request.strategy == "plan_and_execute" and request.step_id == "step-2":
        await _wait_for_release("plan-step-2")
    return ModelDecisionResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        "final",
        f"decision:{request.operation_id}",
        (),
        request.transcript_version + 1,
        "stop",
        "done",
    )


@activity.defn(name="tool_execution_activity")
async def tool_activity(request: ToolExecutionInput) -> ToolExecutionResult:
    first_execution = _record(request.operation_id, "tool", request.plan_version)
    if first_execution:
        with sqlite3.connect(_STATE_DIR / "operations.sqlite") as connection:
            connection.execute(
                "INSERT INTO side_effects(operation_id) VALUES (?)", (request.operation_id,)
            )
            connection.commit()
    if request.strategy == "react":
        await _wait_for_release("react-tool")
    return ToolExecutionResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        f"tool-result:{request.operation_id}",
        request.transcript_version + 1,
        "tool done",
    )


@activity.defn(name="planning_activity")
async def planning_activity(request: PlanningInput) -> PlanningResult:
    _record(request.operation_id, "planning", request.plan_version)
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
async def evaluation_activity(request: PlanEvaluationInput) -> PlanEvaluationResult:
    _record(request.operation_id, "evaluation", request.plan_version)
    return PlanEvaluationResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        "complete" if request.step_index == request.step_count else "continue",
        "test",
    )


async def _serve(args: argparse.Namespace) -> None:
    global _STATE_DIR
    _STATE_DIR = Path(args.state_dir)
    _STATE_DIR.mkdir(parents=True, exist_ok=True)
    client = await Client.connect(args.host, namespace=args.namespace)
    if args.role == "workflow":
        worker = Worker(
            client,
            task_queue=AGENT_TASK_QUEUE,
            workflows=[
                AgentRunWorkflow,
                ReactAgentWorkflow,
                PlanAndExecuteWorkflow,
                AgentStepWorkflow,
            ],
        )
    else:
        worker = Worker(
            client,
            task_queue=AGENT_TASK_QUEUE,
            activities=[
                context_activity,
                model_activity,
                tool_activity,
                planning_activity,
                evaluation_activity,
            ],
        )
    (_STATE_DIR / f"{args.ready_name}.ready").touch()
    await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("workflow", "activity"))
    parser.add_argument("host")
    parser.add_argument("namespace")
    parser.add_argument("state_dir")
    parser.add_argument("ready_name")
    asyncio.run(_serve(parser.parse_args()))


if __name__ == "__main__":
    main()
