"""Canonical lifecycle terminal authority; legacy single-Activity tests replaced in W1-C.

Web execution, lost Start acknowledgements, failure/cancel and both strategies run
through actual PG Activities in web_persistence/test_web_temporal_e2e.py. Process
kill/recovery is tested in test_durable_agent_worker_kill.py.
"""

import os
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client, WorkflowFailureError
from temporalio.worker import Replayer, Worker

from orchestration.agent_lifecycle_workflow import AgentLifecycleWorkflow
from orchestration.run_lifecycle_contracts import WEB_LIFECYCLE_TASK_QUEUE, RunLifecycleInput

_authority = {}


@activity.defn(name="prepare_run_activity")
async def prepare(request: RunLifecycleInput) -> dict[str, str]:
    return {"run_id": request.run_id, "status": _authority[request.run_id]}


@activity.defn(name="finalize_cancelled_activity")
async def cancel(request: RunLifecycleInput) -> dict[str, str]:
    _authority[request.run_id] = "cancelled"
    return {"run_id": request.run_id, "status": "cancelled"}


@pytest.mark.temporal
@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "failed", "cancelling", "cancelled"])
async def test_canonical_prepared_terminal_authority_never_starts_agent(status):
    if not os.getenv("TEMPORAL_HOST"):
        pytest.skip("TEMPORAL_HOST required")
    client = await Client.connect(
        os.environ["TEMPORAL_HOST"], namespace=os.getenv("TEMPORAL_NAMESPACE", "default")
    )
    run_id = str(uuid4())
    _authority[run_id] = status
    async with Worker(
        client,
        task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        workflows=[AgentLifecycleWorkflow],
        activities=[prepare, cancel],
    ):
        handle = await client.start_workflow(
            AgentLifecycleWorkflow.run,
            RunLifecycleInput(1, run_id),
            id=f"terminal-{run_id}",
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
        )
        if status == "completed":
            assert (await handle.result())["outcome"] == "completed"
        else:
            with pytest.raises(WorkflowFailureError):
                await handle.result()
        history = await handle.fetch_history()
        assert not any(
            event.HasField("start_child_workflow_execution_initiated_event_attributes")
            for event in history.events
        )
        await Replayer(workflows=[AgentLifecycleWorkflow]).replay_workflow(history)
