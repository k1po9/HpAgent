"""Composition helpers for the isolated Web Temporal workers.

The caller supplies Activity implementations so the lifecycle worker never
imports or constructs Agent/Sandbox dependencies as a side effect of this
module.  QQ continues to be composed by ``orchestration.worker`` unchanged.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from temporalio.client import Client
from temporalio.worker import Worker

from .web_workflow import (
    WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS,
    WEB_AGENT_HEARTBEAT_TIMEOUT_SECONDS,
    WEB_AGENT_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_AGENT_START_TO_CLOSE_SECONDS,
    WEB_AGENT_TASK_QUEUE,
    WEB_CANCEL_CLEANUP_TIMEOUT_SECONDS,
    WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_FINALIZE_START_TO_CLOSE_SECONDS,
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_PREPARE_START_TO_CLOSE_SECONDS,
    WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS,
    WebRunWorkflow,
)

WEB_REAL_AGENT_GATE_VERSION = "c-07-v1"


@dataclass(frozen=True)
class WebTemporalWorkers:
    lifecycle: Worker
    agent: Worker


def validate_web_outbox_recovery(
    lease_timeout_seconds: int,
    recovery_interval_seconds: float,
) -> None:
    """Outbox auto-recovery config must never produce ``recover_expired(0)``.

    ``lease_timeout_seconds`` and ``recovery_interval_seconds`` must both be
    positive and the interval must be strictly smaller than the timeout, so a
    lease is observed as expired on a later sweep rather than at the exact
    timeout boundary.
    """
    if lease_timeout_seconds <= 0:
        raise ValueError("web_outbox_lease_timeout_seconds must be positive")
    if recovery_interval_seconds <= 0:
        raise ValueError("web_outbox_recovery_interval_seconds must be positive")
    if recovery_interval_seconds >= lease_timeout_seconds:
        raise ValueError(
            "web_outbox_recovery_interval_seconds must be smaller than "
            "web_outbox_lease_timeout_seconds"
        )


def validate_web_worker_startup(config: Any, worker_database_url: str | None) -> None:
    """Fail closed before constructing any real-Agent Web worker."""
    if config.web_real_agent_gate_version != WEB_REAL_AGENT_GATE_VERSION:
        raise RuntimeError(
            f"WEB real Agent requires WEB_REAL_AGENT_GATE_VERSION={WEB_REAL_AGENT_GATE_VERSION}"
        )
    try:
        validate_web_outbox_recovery(
            config.web_outbox_lease_timeout_seconds,
            config.web_outbox_recovery_interval_seconds,
        )
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if not worker_database_url:
        raise RuntimeError("WEB real Agent requires WORKER_DATABASE_URL")
    if config.web_lifecycle_task_queue != WEB_LIFECYCLE_TASK_QUEUE:
        raise RuntimeError("Web lifecycle task queue differs from frozen Workflow contract")
    if config.web_agent_task_queue != WEB_AGENT_TASK_QUEUE:
        raise RuntimeError("Web Agent task queue differs from frozen Workflow contract")
    frozen_values = {
        "web_workflow_execution_timeout_seconds": WEB_WORKFLOW_EXECUTION_TIMEOUT_SECONDS,
        "web_prepare_schedule_to_close_seconds": WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS,
        "web_prepare_start_to_close_seconds": WEB_PREPARE_START_TO_CLOSE_SECONDS,
        "web_agent_schedule_to_close_seconds": WEB_AGENT_SCHEDULE_TO_CLOSE_SECONDS,
        "web_agent_start_to_close_seconds": WEB_AGENT_START_TO_CLOSE_SECONDS,
        "web_agent_heartbeat_interval_seconds": WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS,
        "web_agent_heartbeat_timeout_seconds": WEB_AGENT_HEARTBEAT_TIMEOUT_SECONDS,
        "web_finalize_schedule_to_close_seconds": WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS,
        "web_finalize_start_to_close_seconds": WEB_FINALIZE_START_TO_CLOSE_SECONDS,
        "web_cancel_cleanup_timeout_seconds": WEB_CANCEL_CLEANUP_TIMEOUT_SECONDS,
    }
    for field, expected in frozen_values.items():
        if getattr(config, field) != expected:
            raise RuntimeError(f"{field} differs from frozen Workflow contract")


def build_web_temporal_workers(
    client: Client,
    *,
    lifecycle_activities: Sequence[Any],
    agent_activities: Sequence[Any],
) -> WebTemporalWorkers:
    """Build, but do not start, the Web lifecycle and Agent workers.

    D-02/D-06 provide the concrete Activity lists.  Keeping construction
    separate permits the composition root to start neither worker until the
    C-07 real-Agent gate is enabled.
    """
    return WebTemporalWorkers(
        lifecycle=Worker(
            client,
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
            workflows=[WebRunWorkflow],
            activities=list(lifecycle_activities),
        ),
        agent=Worker(
            client,
            task_queue=WEB_AGENT_TASK_QUEUE,
            workflows=[],
            activities=list(agent_activities),
            # Temporal otherwise throttles heartbeat RPCs to most of the
            # heartbeat timeout, which delays cancellation beyond our budget.
            max_heartbeat_throttle_interval=timedelta(
                seconds=WEB_AGENT_HEARTBEAT_INTERVAL_SECONDS
            ),
        ),
    )
