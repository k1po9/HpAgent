#!/usr/bin/env python3
"""Explicitly capture a real ``WebRunWorkflow`` History as a frozen fixture.

This script runs ONLY when invoked directly.  It is never imported, never
collected by pytest, and never executed by CI.  It:

1. connects to the real Temporal Server at ``TEMPORAL_HOST``;
2. builds the Web lifecycle/Agent workers with stub Activities;
3. starts one minimal happy-path ``WebRunWorkflow`` Execution;
4. waits for it to complete;
5. fetches the full History and writes it as JSON to the fixture path;
6. immediately re-reads that file from disk and offline-replays it, so a
   broken or hand-edited fixture can never be committed silently.

It refuses to overwrite an existing fixture unless ``--force`` is passed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import uuid4

from temporalio import activity
from temporalio.client import Client, WorkflowHistory
from temporalio.worker import Replayer

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE = (
    REPO_ROOT
    / "test"
    / "fixtures"
    / "temporal"
    / "web_run_workflow_v1_completed.json"
)

# The orchestrator imports ``temporalio`` at import time; make the workflow
# and helpers importable regardless of the caller's working directory.
sys.path.insert(0, str(REPO_ROOT / "src"))

from orchestration.web_dispatcher import TemporalClientAdapter, web_workflow_id  # noqa: E402
from orchestration.web_workers import build_web_temporal_workers  # noqa: E402
from orchestration.web_workflow import (  # noqa: E402
    WebRunWorkflow,
    WebRunWorkflowInput,
)


@activity.defn(name="prepare_run_activity")
async def stub_prepare(request: WebRunWorkflowInput) -> dict[str, str]:
    # A faithful minimal stub: the Workflow may dispatch Agent execution
    # only after the database reports the Run as running.
    return {"run_id": request.run_id, "status": "running"}


@activity.defn(name="execute_agent_activity")
async def stub_execute(request: WebRunWorkflowInput) -> dict[str, str]:
    # Successful happy-path terminal commit: the Workflow returns completed.
    return {"run_id": request.run_id, "status": "completed"}


@activity.defn(name="finalize_failed_activity")
async def stub_finalize_failed(request) -> dict[str, str]:
    return {"run_id": request.run_id, "status": "failed"}


@activity.defn(name="finalize_cancelled_activity")
async def stub_finalize_cancelled(request: WebRunWorkflowInput) -> dict[str, str]:
    return {"run_id": request.run_id, "status": "cancelled"}


async def _offline_replay(workflow_id: str, fixture_text: str) -> None:
    """Replay the on-disk fixture without any server, proving it is valid."""
    history = WorkflowHistory.from_json(workflow_id, fixture_text)
    result = await Replayer(workflows=[WebRunWorkflow]).replay_workflow(history)
    if result.replay_failure:
        raise RuntimeError(f"offline replay failed: {result.replay_failure}")
    if len(history.events) < 2:
        raise RuntimeError("captured History is unexpectedly short")


async def capture(host: str, namespace: str, output: Path, force: bool) -> None:
    if output.exists() and not force:
        raise SystemExit(
            f"refusing to overwrite existing fixture {output}; pass --force to replace it"
        )
    client = await Client.connect(host, namespace=namespace)
    workers = build_web_temporal_workers(
        client,
        lifecycle_activities=[
            stub_prepare,
            stub_finalize_failed,
            stub_finalize_cancelled,
        ],
        agent_activities=[stub_execute],
    )

    run_id = str(uuid4())
    workflow_id = web_workflow_id(run_id)
    request = WebRunWorkflowInput(1, run_id)
    print(f"starting WebRunWorkflow workflow_id={workflow_id}")

    async with workers.lifecycle, workers.agent:
        adapter = TemporalClientAdapter(client)
        temporal_run_id = await adapter.start_web_run(workflow_id, request)
        handle = client.get_workflow_handle(workflow_id, run_id=temporal_run_id)
        result = await handle.result()
        if result.get("outcome") != "completed":
            raise RuntimeError(f"unexpected Workflow result: {result}")
        history = await handle.fetch_history()
        history_json = history.to_json()
        print(
            f"Execution completed: temporal_run_id={temporal_run_id} "
            f"events={len(history.events)}"
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(history_json, encoding="utf-8")
    print(f"wrote fixture: {output}")

    # Re-read from disk (not memory) and offline-replay before returning.
    on_disk = output.read_text(encoding="utf-8")
    await _offline_replay(workflow_id, on_disk)
    print("offline replay of on-disk fixture: OK")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host",
        default=os.getenv("TEMPORAL_HOST"),
        help="Temporal gRPC address (default: $TEMPORAL_HOST)",
    )
    parser.add_argument("--namespace", default="default")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="fixture path to write (default: %(default)s)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="overwrite an existing fixture",
    )
    args = parser.parse_args()
    if not args.host:
        parser.error("TEMPORAL_HOST is not set and --host was not provided")
    try:
        asyncio.run(capture(args.host, args.namespace, args.output, args.force))
    except KeyboardInterrupt:
        print("capture interrupted; fixture was not finalized", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
