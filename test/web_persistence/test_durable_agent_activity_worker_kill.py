from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from temporalio.client import Client, WorkflowFailureError
from temporalio.exceptions import ActivityError, ApplicationError

from agent_activities.store import AgentDataStore
from agent_workflows.contracts import (
    AGENT_SCHEMA_VERSION,
    AGENT_TASK_QUEUE,
    CompactToolCall,
    ToolExecutionInput,
)
from web_domain.services import CommandService

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal, pytest.mark.postgres]

_HARNESS = Path(__file__).parents[1] / "support" / "durable_worker_process.py"


async def _wait_for(path: Path, timeout: float = 20) -> None:
    async with asyncio.timeout(timeout):
        while not path.exists():
            await asyncio.sleep(0.05)


async def _start_worker(
    role: str,
    host: str,
    namespace: str,
    state_dir: Path,
    ready_name: str,
    database_url: str | None = None,
) -> asyncio.subprocess.Process:
    environment = os.environ.copy()
    source_path = str(Path(__file__).parents[2] / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        item for item in (source_path, environment.get("PYTHONPATH", "")) if item
    )
    command = [
        sys.executable,
        str(_HARNESS),
        role,
        host,
        namespace,
        str(state_dir),
        ready_name,
    ]
    if database_url is not None:
        command.extend(("--database-url", database_url))
    process = await asyncio.create_subprocess_exec(*command, env=environment)
    await _wait_for(state_dir / f"{ready_name}.ready")
    assert process.returncode is None
    return process


async def _terminate(process: asyncio.subprocess.Process | None) -> None:
    if process is None or process.returncode is not None:
        return
    process.kill()
    await asyncio.wait_for(process.wait(), timeout=5)


async def test_activity_worker_sigkill_redelivers_intent_without_repeating_side_effect(
    tmp_path,
    db,
    account_id,
    database_url,
    worker_database_url,
):
    host = os.getenv("TEMPORAL_HOST")
    if not host:
        pytest.skip("TEMPORAL_HOST is required for real Activity Worker crash acceptance")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    client = await Client.connect(host, namespace=namespace)

    commands = CommandService(database_url)
    conversation = commands.create_conversation(account_id, str(uuid4()), "crash test")
    sent = commands.send_message(
        account_id,
        UUID(conversation["conversation_id"]),
        str(uuid4()),
        "execute non-idempotent write",
    )
    run = sent["run"]
    run_id = run["run_id"]
    session_id = run["session_id"]
    transcript_id = f"transcript:{run_id}"
    store = AgentDataStore(worker_database_url, lease_ttl_seconds=900)
    lease = store.acquire_lease(str(account_id), run_id)

    context_operation = f"{run_id}:react:context"
    assert store.begin_operation(context_operation, run_id, "context") is None
    store.create_transcript(
        transcript_id=transcript_id,
        run_id=run_id,
        account_id=str(account_id),
        conversation_id=conversation["conversation_id"],
        session_id=session_id,
        messages=[{"role": "user", "content": "execute non-idempotent write"}],
        operation_id=context_operation,
    )
    decision_operation = f"{run_id}:react:turn:1:model"
    decision_ref = f"agent-decision:{decision_operation}"
    assert store.begin_operation(decision_operation, run_id, "model") is None
    store.complete_operation(
        decision_operation,
        decision_ref,
        {
            "schema_version": AGENT_SCHEMA_VERSION,
            "operation_id": decision_operation,
            "decision_type": "tool_calls",
            "decision_ref": decision_ref,
            "tool_call_arguments": {"call-1": {"value": 1}},
            "transcript_version": 1,
        },
    )
    operation_id = f"{run_id}:react:turn:1:tool:call-1"
    request = ToolExecutionInput(
        AGENT_SCHEMA_VERSION,
        run_id,
        str(account_id),
        conversation["conversation_id"],
        session_id,
        "react",
        transcript_id,
        1,
        1,
        operation_id,
        lease.fencing_token,
        CompactToolCall("call-1", "counting_write", f"{decision_ref}#call-1"),
    )

    workflow_worker = first_activity_worker = replacement_activity_worker = None
    try:
        workflow_worker = await _start_worker(
            "workflow", host, namespace, tmp_path, "workflow"
        )
        first_activity_worker = await _start_worker(
            "production-activity",
            host,
            namespace,
            tmp_path,
            "activity-1",
            worker_database_url,
        )
        handle = await client.start_workflow(
            "activity-crash-tool-workflow",
            request,
            id=f"activity-worker-kill-{run_id}",
            task_queue=AGENT_TASK_QUEUE,
        )
        await _wait_for(tmp_path / "activity-side-effect.boundary")
        before = db.execute(
            "SELECT status,attempt_count FROM agent_operations WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        assert before == ("intent_recorded", 1)

        await _terminate(first_activity_worker)
        replacement_activity_worker = await _start_worker(
            "production-activity",
            host,
            namespace,
            tmp_path,
            "activity-2",
            worker_database_url,
        )
        with pytest.raises(WorkflowFailureError) as failure:
            await asyncio.wait_for(handle.result(), timeout=20)
        assert isinstance(failure.value.cause, ActivityError)
        assert isinstance(failure.value.cause.cause, ApplicationError)
        assert failure.value.cause.cause.type == "tool_side_effect_uncertain"

        after = db.execute(
            "SELECT status,error_code,attempt_count,result_payload->>'side_effect_class' "
            "FROM agent_operations WHERE operation_id=%s",
            (operation_id,),
        ).fetchone()
        assert after == (
            "uncertain",
            "tool_side_effect_uncertain",
            2,
            "non_idempotent_write",
        )
        with sqlite3.connect(tmp_path / "external.sqlite") as connection:
            side_effect_count = connection.execute(
                "SELECT count FROM side_effect_counter WHERE operation_id=?",
                (operation_id,),
            ).fetchone()[0]
        assert side_effect_count == 1
    finally:
        await _terminate(replacement_activity_worker)
        await _terminate(first_activity_worker)
        await _terminate(workflow_worker)
