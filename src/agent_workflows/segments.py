"""One active Activity attempt per segment; durable waits hold no lease.

The Workflow retains only stable inputs. Each dispatched Activity receives the
fresh fence for its own segment. Retries release before using a Workflow timer.
"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, TypeVar

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError, ApplicationError, is_cancelled_exception

from .contracts import AGENT_TASK_QUEUE
from .lifecycle_contracts import (
    LIFECYCLE_SCHEMA_VERSION,
    FinishWaitInput,
    SegmentInput,
    SegmentLease,
    WaitInput,
)

_CONTROL_RETRY = RetryPolicy(maximum_attempts=0)
_SINGLE_ATTEMPT = RetryPolicy(maximum_attempts=1)
T = TypeVar("T")


async def _control(name: str, request: Any, result_type: Any = None):
    try:
        return await workflow.execute_activity(
            name,
            request,
            task_queue=AGENT_TASK_QUEUE,
            result_type=result_type,
            start_to_close_timeout=timedelta(seconds=20),
            retry_policy=_CONTROL_RETRY,
            cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
        )
    except ActivityError as exc:
        if isinstance(exc.cause, ApplicationError) and exc.cause.type == "run_not_executable":
            raise asyncio.CancelledError from exc
        raise


async def execute_segment(name: str, request: Any, **options: Any):
    options.setdefault("heartbeat_timeout", timedelta(seconds=45))
    retry = options.pop("retry_policy", RetryPolicy(maximum_attempts=3))
    options.pop("cancellation_type", None)
    attempts = retry.maximum_attempts or 3
    for attempt in range(1, attempts + 1):
        segment = SegmentInput(
            LIFECYCLE_SCHEMA_VERSION,
            request.run_id,
            request.account_id,
            str(workflow.uuid4()),
        )
        error: ActivityError | None = None
        # Cleanup by segment ID also covers acquire committed but result lost.
        try:
            while True:
                lease = await _control("acquire_agent_segment_activity", segment, SegmentLease)
                if lease.acquired:
                    break
                await workflow.sleep(1)
            current = replace(request, lease_token=lease.fencing_token, execution_attempt=attempt)
            try:
                return await workflow.execute_activity(
                    name,
                    current,
                    **options,
                    retry_policy=_SINGLE_ATTEMPT,
                    cancellation_type=workflow.ActivityCancellationType.WAIT_CANCELLATION_COMPLETED,
                )
            except ActivityError as exc:
                error = exc
        finally:
            await _control("release_agent_segment_activity", segment)
        assert error is not None
        if is_cancelled_exception(error):
            raise asyncio.CancelledError from error
        cause = error.cause
        if isinstance(cause, ApplicationError):
            if cause.type == "run_not_executable":
                raise asyncio.CancelledError from error
            if cause.non_retryable and cause.type != "stale_fencing_token":
                raise error
            if cause.type in (retry.non_retryable_error_types or ()):
                raise error
        if attempt == attempts:
            raise error
        delay = retry.initial_interval.total_seconds() * retry.backoff_coefficient ** (attempt - 1)
        if retry.maximum_interval:
            delay = min(delay, retry.maximum_interval.total_seconds())
        # No active segment, workspace lock or Activity across retry backoff.
        await workflow.sleep(delay)


class DurableWait:
    """Signals notify; a caller-supplied authoritative probe decides readiness.

    Model Review, tool approval, callback and human confirmation use a probe.
    Rate-limit/timer users may omit it and resume at the deadline. A bounded
    Workflow timer also recovers missing notifications. No polling Activity
    remains alive between probes.
    """

    def __init__(self):
        self.wait_id: str | None = None
        self.generation = 0

    def notify(self, wait_id: str) -> None:
        if wait_id == self.wait_id:
            self.generation += 1

    async def run(
        self,
        request: WaitInput,
        probe: Callable[[], Awaitable[T | None]] | None = None,
    ) -> T | None:
        self.wait_id = request.wait_id
        state = "cancelled"
        try:
            await _control("begin_agent_wait_activity", request)
            deadline = datetime.fromisoformat(request.deadline)
            while True:
                observed = self.generation
                if probe is not None:
                    value = await probe()
                    if value is not None:
                        state = "resumed"
                        return value
                remaining = (deadline - workflow.now()).total_seconds()
                if remaining <= 0:
                    if probe is None:
                        state = "resumed"
                        return None
                    state = "expired"
                    raise TimeoutError("durable wait deadline reached")
                try:
                    await workflow.wait_condition(
                        lambda: self.generation != observed,
                        timeout=min(remaining, 30),
                    )
                except TimeoutError:
                    pass
        finally:
            active = await _control(
                "finish_agent_wait_activity", FinishWaitInput(request, state), bool
            )
            self.wait_id = None
            if state == "resumed" and not active:
                raise asyncio.CancelledError
