"""Subprocess worker used by the real Temporal worker-restart acceptance tests."""
from __future__ import annotations

import argparse
import asyncio
import sqlite3
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from temporalio.worker import Worker

from agent.protocol import ActionResult
from agent_activities.runtime import DurableAgentActivities
from agent_activities.side_effects import UnsupportedToolSideEffectReconciler
from agent_activities.store import AgentDataStore
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


class _Events:
    async def progress(self, phase: str, summary: str) -> None:
        return None

    async def close(self) -> None:
        return None


class _EventFactory:
    def for_run(self, run_id: str) -> _Events:
        return _Events()


class _ResourcePrep:
    @asynccontextmanager
    async def lease_for_run(self, account_id, run_id, control):
        yield


class _CountingNonIdempotentActions:
    def side_effect_class(self, session_id: str, tool_name: str) -> str:
        return "non_idempotent_write"

    async def execute_request(self, request, **kwargs) -> ActionResult:
        with sqlite3.connect(_STATE_DIR / "external.sqlite") as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS side_effect_counter ("
                "operation_id TEXT PRIMARY KEY, count INTEGER NOT NULL)"
            )
            connection.execute(
                "INSERT INTO side_effect_counter(operation_id,count) VALUES (?,1) "
                "ON CONFLICT(operation_id) DO UPDATE SET count=count+1",
                (kwargs["idempotency_key"],),
            )
            connection.commit()
        (_STATE_DIR / "activity-side-effect.boundary").touch()
        while True:
            activity.heartbeat({"boundary": "activity-side-effect"})
            await asyncio.sleep(0.1)

    def clear_execution(self, session_id: str, run_id: str) -> None:
        return None


@workflow.defn(name="activity-crash-tool-workflow")
class ActivityCrashToolWorkflow:
    @workflow.run
    async def run(self, request: ToolExecutionInput) -> ToolExecutionResult:
        return await workflow.execute_activity(
            "tool_execution_activity",
            request,
            task_queue=AGENT_TASK_QUEUE,
            result_type=ToolExecutionResult,
            start_to_close_timeout=timedelta(seconds=30),
            heartbeat_timeout=timedelta(seconds=2),
            retry_policy=RetryPolicy(
                initial_interval=timedelta(milliseconds=200),
                maximum_attempts=3,
            ),
        )


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
                ActivityCrashToolWorkflow,
            ],
        )
    elif args.role == "activity":
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
    else:
        if not args.database_url:
            raise ValueError("production-activity requires --database-url")
        durable = DurableAgentActivities(
            store=AgentDataStore(args.database_url, lease_ttl_seconds=900),
            loader=None,
            brain=None,
            actions=_CountingNonIdempotentActions(),
            event_factory=_EventFactory(),
            resource_prep=_ResourcePrep(),
            lifecycle=None,
            reconciler=UnsupportedToolSideEffectReconciler(),
        )
        worker = Worker(
            client,
            task_queue=AGENT_TASK_QUEUE,
            activities=[durable.tool_execution],
        )
    (_STATE_DIR / f"{args.ready_name}.ready").touch()
    await worker.run()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=("workflow", "activity", "production-activity"))
    parser.add_argument("host")
    parser.add_argument("namespace")
    parser.add_argument("state_dir")
    parser.add_argument("ready_name")
    parser.add_argument("--database-url")
    asyncio.run(_serve(parser.parse_args()))


if __name__ == "__main__":
    main()
