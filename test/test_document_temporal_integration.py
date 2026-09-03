from __future__ import annotations

import json
import os
from contextlib import AsyncExitStack
from uuid import uuid4

import pytest
from temporalio import activity
from temporalio.client import Client
from temporalio.worker import Replayer, Worker

from document_activities.contracts import NormalizeDocumentInput
from file_runtime.routing import TemporalDocumentRouter
from orchestration.document_contracts import DOCUMENT_TASK_QUEUE
from orchestration.document_workflow import NormalizeDocumentWorkflow
from sandbox.tools.local.file_read import create_file_read_tools
from workspace.file_scope import RunFileInput, RunFileScope

pytestmark = [pytest.mark.asyncio, pytest.mark.temporal]


@activity.defn(name="normalize_document_activity")
async def normalize_document(request: NormalizeDocumentInput):
    return {
        "schema_version": 1,
        "run_id": request.run_id,
        "file_id": request.file_id,
        "document_ref": f"normalized-document:{request.run_id}:{request.file_id}",
        "block_count": 10,
        "table_count": 2,
        "truncated": False,
    }


async def test_document_workflow_routes_to_dedicated_queue_with_compact_history() -> None:
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")
    client = await Client.connect(temporal_host)
    lifecycle_queue = f"document-test-{uuid4()}"
    lifecycle = Worker(
        client, task_queue=lifecycle_queue, workflows=[NormalizeDocumentWorkflow]
    )
    document = Worker(
        client, task_queue=DOCUMENT_TASK_QUEUE, activities=[normalize_document]
    )
    request = NormalizeDocumentInput(
        1, str(uuid4()), str(uuid4()), str(uuid4()), f"document-test:{uuid4()}"
    )
    workflow_id = f"hpagent-document-test-{uuid4()}"
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(lifecycle)
        await stack.enter_async_context(document)
        handle = await client.start_workflow(
            NormalizeDocumentWorkflow.run,
            request,
            id=workflow_id,
            task_queue=lifecycle_queue,
        )
        result = await handle.result()
        history = await handle.fetch_history()

    assert set(result) == {
        "schema_version", "run_id", "file_id", "document_ref",
        "block_count", "table_count", "truncated",
    }
    assert result["document_ref"].startswith("normalized-document:")
    replay = await Replayer(workflows=[NormalizeDocumentWorkflow]).replay_workflow(history)
    assert replay.replay_failure is None


async def test_large_document_read_tool_routes_through_real_document_worker(
    tmp_path,
) -> None:
    temporal_host = os.getenv("TEMPORAL_HOST")
    if not temporal_host:
        pytest.skip("TEMPORAL_HOST is required")
    client = await Client.connect(temporal_host)
    lifecycle_queue = f"document-route-test-{uuid4()}"
    lifecycle = Worker(
        client, task_queue=lifecycle_queue, workflows=[NormalizeDocumentWorkflow]
    )
    document = Worker(
        client, task_queue=DOCUMENT_TASK_QUEUE, activities=[normalize_document]
    )
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    content = b"large deterministic document"
    (inputs / "large.txt").write_bytes(content)
    run_id, file_id = uuid4(), uuid4()
    scope = RunFileScope(
        run_id, inputs, tmp_path / "scratch", tmp_path / "outputs",
        (RunFileInput(file_id, "large.txt", len(content), "utf-8", "text/plain"),),
    )
    router = TemporalDocumentRouter(
        client, direct_read_max_bytes=1, workflow_task_queue=lifecycle_queue
    )
    tool = next(tool for tool in create_file_read_tools(
        lambda: scope, document_router=router,
        account_id_provider=lambda: str(uuid4()),
    ) if tool.name == "fast_text_view")
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(lifecycle)
        await stack.enter_async_context(document)
        result = json.loads(await tool.ainvoke({"file": "large.txt"}))

    assert result["route"] == "normalized_document"
    assert result["normalized_document_ref"]["file_id"] == str(file_id)
