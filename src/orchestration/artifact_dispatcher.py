from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError

from common.logging import log_event
from web_artifacts.models import ArtifactBuildInput
from web_artifacts.outbox import ArtifactOutboxService

from .artifact_workflow import ARTIFACT_TASK_QUEUE, ArtifactBuildWorkflow

logger = logging.getLogger("HpAgent.ArtifactOutboxDispatcher")


def artifact_workflow_id(version_id: UUID | str) -> str:
    return f"hpagent-web-artifact-{version_id}"


class TemporalArtifactClient:
    def __init__(self, client: Client):
        self.client = client

    async def start(self, version_id: UUID) -> None:
        workflow_id = artifact_workflow_id(version_id)
        try:
            await self.client.start_workflow(
                ArtifactBuildWorkflow.run, ArtifactBuildInput(1, str(version_id)),
                id=workflow_id, task_queue=ARTIFACT_TASK_QUEUE,
                id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
                id_conflict_policy=WorkflowIDConflictPolicy.FAIL,
                retry_policy=None,
            )
        except WorkflowAlreadyStartedError as exc:
            if exc.workflow_id != workflow_id or exc.workflow_type != "ArtifactBuildWorkflow":
                raise RuntimeError(
                    "deterministic Artifact Workflow ID belongs to another Workflow"
                ) from exc
            return


class ArtifactOutboxDispatcher:
    def __init__(self, outbox: ArtifactOutboxService, temporal: TemporalArtifactClient,
                 worker_id: str, *, max_attempts: int = 10):
        self.outbox, self.temporal, self.worker_id = outbox, temporal, worker_id
        self.max_attempts = max_attempts

    async def run_once(self, limit: int = 10) -> int:
        events = await asyncio.to_thread(self.outbox.claim, self.worker_id, limit)
        for event in events:
            event_id = UUID(str(event["artifact_outbox_event_id"]))
            version_id = UUID(str(event["artifact_version_id"]))
            log_event(logger, logging.INFO, "artifact_outbox_claimed", "artifact_dispatcher",
                      artifact_outbox_event_id=str(event_id),
                      artifact_version_id=str(version_id),
                      attempt_count=int(event["attempt_count"]))
            try:
                await self.temporal.start(version_id)
                await asyncio.to_thread(self.outbox.mark_processed, event_id, self.worker_id)
                log_event(logger, logging.INFO, "artifact_outbox_processed",
                          "artifact_dispatcher", artifact_outbox_event_id=str(event_id),
                          artifact_version_id=str(version_id), status="success")
            except Exception as exc:
                if int(event["attempt_count"]) >= self.max_attempts:
                    await asyncio.to_thread(self.outbox.dead_letter, event_id, self.worker_id,
                                            "artifact_dispatch_exhausted", str(exc)[:1000])
                    await asyncio.to_thread(
                        self.outbox.fail_version, version_id, "artifact_build_failed",
                        "Artifact 调度失败。",
                    )
                    log_event(logger, logging.ERROR, "artifact_outbox_dead_letter",
                              "artifact_dispatcher",
                              artifact_outbox_event_id=str(event_id),
                              artifact_version_id=str(version_id), status="failed",
                              failure_code="artifact_dispatch_exhausted")
                else:
                    await asyncio.to_thread(
                        self.outbox.retry, event_id, self.worker_id,
                        "artifact_dispatch_failed", str(exc)[:1000],
                        datetime.now(UTC) + timedelta(seconds=5),
                    )
                    log_event(logger, logging.WARNING, "artifact_outbox_retry",
                              "artifact_dispatcher",
                              artifact_outbox_event_id=str(event_id),
                              artifact_version_id=str(version_id), status="retrying",
                              failure_code="artifact_dispatch_failed")
        return len(events)


async def run_artifact_dispatcher_loop(dispatcher: ArtifactOutboxDispatcher,
                                       idle_seconds: float = 0.25) -> None:
    while True:
        try:
            if not await dispatcher.run_once():
                await asyncio.sleep(idle_seconds)
        except asyncio.CancelledError:
            raise
        except Exception:
            await asyncio.sleep(1)


async def run_artifact_outbox_recovery_loop(outbox: ArtifactOutboxService,
                                             lease_timeout_seconds: int,
                                             interval_seconds: float) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        await asyncio.to_thread(outbox.recover_expired, lease_timeout_seconds)
