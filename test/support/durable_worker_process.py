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
from agent_workflows.tool_execution import ToolExecutionWorkflow

_STATE_DIR: Path
_FAULT_BOUNDARY: str | None = None
_ACTIVITY_CASE: str | None = None


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
        (_STATE_DIR / "activity-side-effect-succeeded-before-ack.boundary").touch()
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


async def _wait_at_fault_boundary(name: str) -> None:
    legacy_boundaries = {"react-tool", "plan-step-2"}
    if _FAULT_BOUNDARY == name or (
        _FAULT_BOUNDARY is None and name in legacy_boundaries
    ):
        await _wait_for_release(name)


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
        await _wait_at_fault_boundary("react-model-decision")
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
    if request.strategy == "react" and request.final_only:
        await _wait_at_fault_boundary("react-final-synthesis")
    if request.strategy == "plan_and_execute" and request.step_id == "step-2":
        await _wait_at_fault_boundary("plan-step-2")
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
    if request.strategy == "react":
        await _wait_at_fault_boundary("react-tool-before-side-effect")
    first_execution = _record(request.operation_id, "tool", request.plan_version)
    if first_execution:
        with sqlite3.connect(_STATE_DIR / "operations.sqlite") as connection:
            connection.execute(
                "INSERT INTO side_effects(operation_id) VALUES (?)", (request.operation_id,)
            )
            connection.commit()
    if request.strategy == "react":
        await _wait_at_fault_boundary("react-tool")
        await _wait_at_fault_boundary("react-tool-after-side-effect-before-ack")
    return ToolExecutionResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        f"tool-result:{request.operation_id}",
        request.transcript_version + 1,
        "tool done",
    )


def _record_activity_attempt(operation_id: str, case_id: str) -> int:
    with sqlite3.connect(_STATE_DIR / "activity_recovery.sqlite") as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS activity_attempts ("
            "attempt_id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL, "
            "case_id TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
        connection.execute(
            "INSERT INTO activity_attempts(operation_id,case_id) VALUES (?,?)",
            (operation_id, case_id),
        )
        attempt = connection.execute(
            "SELECT count(*) FROM activity_attempts WHERE operation_id=?",
            (operation_id,),
        ).fetchone()[0]
        connection.commit()
        return int(attempt)


def _record_idempotent_effect(operation_id: str) -> None:
    with sqlite3.connect(_STATE_DIR / "activity_recovery.sqlite") as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS idempotent_business_state ("
            "operation_id TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS effect_invocations ("
            "invocation_id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO idempotent_business_state(operation_id,value) VALUES (?,?) "
            "ON CONFLICT(operation_id) DO UPDATE SET value=excluded.value",
            (operation_id, "expected-result"),
        )
        connection.execute(
            "INSERT INTO effect_invocations(operation_id) VALUES (?)", (operation_id,)
        )
        connection.commit()


@activity.defn(name="tool_execution_activity")
async def benchmark_tool_activity(request: ToolExecutionInput) -> ToolExecutionResult:
    if _ACTIVITY_CASE not in {"A1", "A2"}:
        raise RuntimeError("benchmark Activity case is not configured")
    attempt = _record_activity_attempt(request.operation_id, _ACTIVITY_CASE)
    if _ACTIVITY_CASE == "A1" and attempt == 1:
        await _wait_for_release("activity-before-side-effect")
    if _ACTIVITY_CASE == "A2":
        _record_idempotent_effect(request.operation_id)
        if attempt == 1:
            await _wait_for_release("activity-idempotent-effect-succeeded-before-ack")
    return ToolExecutionResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        f"tool-result:{request.operation_id}",
        request.transcript_version + 1,
        "expected-result",
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
    if request.step_index == request.step_count:
        await _wait_at_fault_boundary("plan-final-evaluation")
    return PlanEvaluationResult(
        AGENT_SCHEMA_VERSION,
        request.operation_id,
        "complete" if request.step_index == request.step_count else "continue",
        "test",
    )


async def _serve(args: argparse.Namespace) -> None:
    global _ACTIVITY_CASE, _FAULT_BOUNDARY, _STATE_DIR
    _STATE_DIR = Path(args.state_dir)
    _FAULT_BOUNDARY = args.fault_boundary
    _ACTIVITY_CASE = args.activity_case
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
                ToolExecutionWorkflow,
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
    elif args.role == "benchmark-activity":
        if args.activity_case not in {"A1", "A2"}:
            raise ValueError("benchmark-activity requires --activity-case A1 or A2")
        worker = Worker(
            client,
            task_queue=AGENT_TASK_QUEUE,
            activities=[benchmark_tool_activity],
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
    parser.add_argument(
        "role",
        choices=("workflow", "activity", "benchmark-activity", "production-activity"),
    )
    parser.add_argument("host")
    parser.add_argument("namespace")
    parser.add_argument("state_dir")
    parser.add_argument("ready_name")
    parser.add_argument("--database-url")
    parser.add_argument("--fault-boundary")
    parser.add_argument("--activity-case")
    asyncio.run(_serve(parser.parse_args()))


if __name__ == "__main__":
    main()
