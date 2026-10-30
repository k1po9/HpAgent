"""Execution fence propagated into short data-plane transactions and threads."""

import asyncio
from functools import wraps

from temporalio import activity
from temporalio.exceptions import ApplicationError

from run_domain.execution_context import fence_scope


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
        with fence_scope(request.account_id, request.run_id, request.lease_token, request.execution_id):
            heartbeat = asyncio.create_task(_heartbeat_segment(request))
            try:
                return await function(self, request)
            except Exception as exc:
                if getattr(exc, "code", None) in {
                    "stale_fencing_token", "memory_context_revoked", "no_progress", "repeated_failure",
                    "run_budget_exhausted", "work_budget_exhausted",
                    "capability_budget_exhausted", "account_daily_model_budget_exhausted",
                    "tool_side_effect_uncertain",
                }:
                    raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
                raise
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    return wrapped
