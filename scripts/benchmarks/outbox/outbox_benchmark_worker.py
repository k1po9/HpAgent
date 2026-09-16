"""Real subprocess worker for the Transactional Outbox recovery benchmark."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import AsyncExitStack
from dataclasses import asdict
from pathlib import Path
from uuid import UUID

from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Worker

from agent_activities.segments import SegmentActivities
from agent_activities.store import AgentDataStore
from agent_workflows.agent_run import AgentRunWorkflow
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AgentRunInput,
    ContextBootstrapInput,
    ContextBootstrapResult,
    FinalizeResultInput,
    ModelDecisionInput,
    ModelDecisionResult,
)
from agent_workflows.react import ReactAgentWorkflow
from conversation_domain.run_input import ChatRunInputLoader
from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_lifecycle_contracts import (
    WEB_AGENT_TASK_QUEUE,
    WEB_LIFECYCLE_TASK_QUEUE,
    FailureInput,
)
from orchestration.run_lifecycle_contracts import (
    RunLifecycleInput as WebRunWorkflowInput,
)
from orchestration.web_dispatcher import (
    TemporalClientAdapter,
    TemporalOutboxDispatcher,
    WebOutboxDispatcher,
)
from web_domain.lifecycle import WebRunLifecycleService
from web_domain.outbox import OutboxService
from web_domain.workflow_execution import PostgresWorkflowExecutionStore

_LIFECYCLE: WebRunLifecycleService
_FAKE_DELAY_SECONDS: float


@activity.defn(name="prepare_run_activity")
async def prepare_run(request: WebRunWorkflowInput) -> dict[str, str]:
    info = activity.info()
    authority = await asyncio.to_thread(
        _LIFECYCLE.prepare,
        UUID(request.run_id),
        info.workflow_id,
        info.workflow_run_id,
    )
    return asdict(authority)


_INPUT_LOADER: ChatRunInputLoader


@activity.defn(name="load_agent_run_input_activity")
async def load_input(request: WebRunWorkflowInput) -> AgentRunInput:
    return await asyncio.to_thread(_INPUT_LOADER.load, request.run_id)


@activity.defn(name="context_bootstrap_activity")
async def benchmark_context(request: ContextBootstrapInput) -> ContextBootstrapResult:
    return ContextBootstrapResult(AGENT_SCHEMA_VERSION, request.run_id, 1, request.run_id)


@activity.defn(name="model_decision_activity")
async def benchmark_model(request: ModelDecisionInput) -> ModelDecisionResult:
    await asyncio.sleep(_FAKE_DELAY_SECONDS)
    return ModelDecisionResult(AGENT_SCHEMA_VERSION, request.operation_id, "final",
                               request.run_id, (), 2, "stop", "benchmark reply")


@activity.defn(name="finalize_agent_result_activity")
async def complete_agent(request: FinalizeResultInput) -> dict[str, str]:
    authority = await asyncio.to_thread(_LIFECYCLE.complete, UUID(request.run_id),
                                        f"outbox benchmark completed: {request.run_id}")
    return asdict(authority)


@activity.defn(name="finalize_failed_activity")
async def finalize_failed(failure: FailureInput) -> dict[str, str]:
    authority = await asyncio.to_thread(
        _LIFECYCLE.finalize_failed,
        UUID(failure.run_id),
        failure.error_code,
        failure.error_message,
    )
    return asdict(authority)


@activity.defn(name="finalize_cancelled_activity")
async def finalize_cancelled(request: WebRunWorkflowInput) -> dict[str, str]:
    authority = await asyncio.to_thread(
        _LIFECYCLE.finalize_cancelled, UUID(request.run_id)
    )
    return asdict(authority)


async def serve(args: argparse.Namespace) -> None:
    global _FAKE_DELAY_SECONDS, _LIFECYCLE, _INPUT_LOADER
    _FAKE_DELAY_SECONDS = args.fake_delay_seconds
    _LIFECYCLE = WebRunLifecycleService(args.worker_database_url)
    client = await Client.connect(args.temporal_host, namespace=args.temporal_namespace)
    store = AgentDataStore(args.worker_database_url)
    _INPUT_LOADER = ChatRunInputLoader(store)
    segments = SegmentActivities(store)
    lifecycle_worker = Worker(
        client,
        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[AgentLifecycleWorkflow],
        activities=[prepare_run, load_input, complete_agent, finalize_failed, finalize_cancelled],
    )
    agent_worker = Worker(
        client,
        task_queue=WEB_AGENT_TASK_QUEUE,
        workflows=[AgentRunWorkflow, ReactAgentWorkflow],
        activities=[segments.acquire, segments.release, benchmark_context, benchmark_model],
    )
    outbox = OutboxService(args.worker_database_url)
    dispatcher = WebOutboxDispatcher(
        outbox,
        TemporalOutboxDispatcher(
            PostgresWorkflowExecutionStore(args.worker_database_url),
            TemporalClientAdapter(client),
        ),
        args.worker_id,
    )
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(lifecycle_worker)
        await stack.enter_async_context(agent_worker)
        Path(args.ready_file).touch()
        while True:
            processed = await dispatcher.run_once(args.batch_size)
            if processed == 0:
                await asyncio.sleep(args.poll_seconds)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--temporal-host", required=True)
    parser.add_argument("--temporal-namespace", required=True)
    parser.add_argument("--worker-database-url", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--worker-id", default="outbox-benchmark-worker")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    parser.add_argument("--fake-delay-seconds", type=float, default=0.02)
    return parser.parse_args()


def main() -> None:
    asyncio.run(serve(parse_args()))


if __name__ == "__main__":
    main()
