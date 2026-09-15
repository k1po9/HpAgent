"""Deterministic Temporal workflow contract for one Web domain Run.

This module deliberately has no dependency on the database, Redis, the model,
or the legacy QQ execution chain.  Activities own all side effects.
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

from .run_lifecycle_contracts import (
    _AGENT_NO_RETRY,
    _FINALIZE_RETRY,
    _LIFECYCLE_RETRY,
    _STABLE_FAILURE_MESSAGES,
    WEB_AGENT_HEARTBEAT_TIMEOUT_SECONDS,
    WEB_AGENT_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_AGENT_START_TO_CLOSE_SECONDS,
    WEB_AGENT_TASK_QUEUE,
    WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_FINALIZE_START_TO_CLOSE_SECONDS,
    WEB_LIFECYCLE_TASK_QUEUE,
    WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS,
    WEB_PREPARE_START_TO_CLOSE_SECONDS,
    WEB_WORKFLOW_SCHEMA_VERSION,
    FailureInput,
    RunAuthority,
)
from .run_lifecycle_contracts import (
    RunLifecycleInput as WebRunWorkflowInput,
)


@workflow.defn
class WebRunWorkflow:
    """Run one Web Agent execution without putting business data in History.

    D-01 establishes the command order and retry/queue contract.  D-02 adds
    the concrete lifecycle activities and D-07 adds cancellation finalization.
    Keeping the command order here small and stable makes replay tests useful.
    """

    @workflow.run
    async def run(self, request: WebRunWorkflowInput) -> dict[str, str | int]:
        request.validate()
        try:
            prepared: RunAuthority = await workflow.execute_activity(
                "prepare_run_activity", request, task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_PREPARE_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_PREPARE_START_TO_CLOSE_SECONDS),
                retry_policy=_LIFECYCLE_RETRY,
            )
            status = prepared["status"]
            if status == "completed":
                return self._completed(request.run_id)
            if status in ("cancelling", "cancelled"):
                # The database is authoritative, but only the Temporal Cancel
                # request may close this execution as Canceled.  Waiting here
                # prevents Agent dispatch in the Start/Cancel race window.
                await workflow.wait_condition(lambda: False)
                raise AssertionError("unreachable cancellation wait")
            if status == "failed":
                raise ApplicationError("run already failed", non_retryable=True)
            result: RunAuthority = await workflow.execute_activity(
                "execute_agent_activity", request, task_queue=WEB_AGENT_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_AGENT_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_AGENT_START_TO_CLOSE_SECONDS),
                heartbeat_timeout=timedelta(seconds=WEB_AGENT_HEARTBEAT_TIMEOUT_SECONDS),
                retry_policy=_AGENT_NO_RETRY,
                cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
            )
        except asyncio.CancelledError:
            # A cancellation request is an execution fact, not sufficient
            # proof of a user cancellation.  D-02's lifecycle transaction
            # makes the authoritative decision before this Workflow closes.
            authority: RunAuthority = await workflow.execute_activity(
                "finalize_cancelled_activity",
                request,
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                retry_policy=_FINALIZE_RETRY,
            )
            if authority["status"] == "completed":
                return self._completed(request.run_id)
            if authority["status"] == "failed":
                raise ApplicationError("unexpected workflow cancellation", non_retryable=True)
            raise
        except ActivityError as exc:
            # With WAIT_CANCELLATION_COMPLETED the SDK reports the cancelled
            # Activity as ActivityError(cause=CancelledError), not necessarily
            # as asyncio.CancelledError.  Classify it before ordinary failure.
            if is_cancelled_exception(exc):
                authority = await workflow.execute_activity(
                    "finalize_cancelled_activity",
                    request,
                    task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                    schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                    start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                    retry_policy=_FINALIZE_RETRY,
                )
                if authority["status"] == "completed":
                    return self._completed(request.run_id)
                if authority["status"] == "failed":
                    raise ApplicationError(
                        "unexpected workflow cancellation", non_retryable=True
                    ) from exc
                raise asyncio.CancelledError from exc
            failure_type = getattr(exc.cause, "type", "")
            error_code = (
                failure_type
                if failure_type in _STABLE_FAILURE_MESSAGES
                else "internal_execution_error"
            )
            authority = await workflow.execute_activity(
                "finalize_failed_activity",
                FailureInput(
                    1,
                    request.run_id,
                    error_code,
                    _STABLE_FAILURE_MESSAGES.get(error_code, "执行未能完成。"),
                ),
                task_queue=WEB_LIFECYCLE_TASK_QUEUE,
                schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
                start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
                retry_policy=_FINALIZE_RETRY,
            )
            # Covers completion commit succeeded but the Agent Activity ack was
            # lost.  Domain truth wins and Temporal completes successfully.
            if authority["status"] == "completed":
                return self._completed(request.run_id)
            raise
        if result["status"] == "completed":
            return self._completed(request.run_id)
        # An Agent activity must commit success itself.  Any other returned
        # state is a protocol violation and is centralized by D-02.
        authority = await workflow.execute_activity(
            "finalize_failed_activity",
            FailureInput(1, request.run_id, "internal_execution_error", "执行未能完成。"),
            task_queue=WEB_LIFECYCLE_TASK_QUEUE,
            schedule_to_close_timeout=timedelta(seconds=WEB_FINALIZE_SCHEDULE_TO_CLOSE_SECONDS),
            start_to_close_timeout=timedelta(seconds=WEB_FINALIZE_START_TO_CLOSE_SECONDS),
            retry_policy=_FINALIZE_RETRY,
        )
        if authority["status"] == "completed":
            return self._completed(request.run_id)
        raise ApplicationError("agent activity returned non-completed status", non_retryable=True)

    @staticmethod
    def _completed(run_id: str) -> dict[str, str | int]:
        return {
            "schema_version": WEB_WORKFLOW_SCHEMA_VERSION,
            "run_id": run_id,
            "outcome": "completed",
        }
