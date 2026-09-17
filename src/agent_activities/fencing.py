"""Execution fence propagated into short data-plane transactions and threads."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

from temporalio import activity
from temporalio.exceptions import ApplicationError

execution_fence: ContextVar[tuple[str, str, int] | None] = ContextVar(
    "execution_fence", default=None
)


@contextmanager
def fence_scope(account_id: str, run_id: str, token: int):
    reset = execution_fence.set((account_id, run_id, token))
    try:
        yield
    finally:
        execution_fence.reset(reset)


async def _heartbeat_segment(request):
    while True:
        try:
            activity.heartbeat({"run_id": request.run_id, "operation_id": request.operation_id})
        except RuntimeError:
            return  # Direct contract tests have no Activity context.
        await asyncio.sleep(5)


def fenced_activity(function):
    @wraps(function)
    async def wrapped(self, request):
        with fence_scope(request.account_id, request.run_id, request.lease_token):
            heartbeat = asyncio.create_task(_heartbeat_segment(request))
            try:
                return await function(self, request)
            except Exception as exc:
                if getattr(exc, "code", None) == "stale_fencing_token":
                    raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
                raise
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    return wrapped
