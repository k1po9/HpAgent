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
                code = exc.type if isinstance(exc, ApplicationError) else getattr(exc, "code", None)
                if code in {
                    "stale_fencing_token", "memory_context_revoked", "no_progress", "repeated_failure",
                    "run_budget_exhausted", "work_budget_exhausted",
                    "capability_budget_exhausted", "account_daily_model_budget_exhausted",
                    "tool_side_effect_uncertain",
                }:
                    # Stable codes and execution identity are diagnostic data;
                    # exception text can contain tool input or provider secrets.
                    raise ApplicationError(
                        "Activity rejected: " + code,
                        {
                            "schema_version": 1, "boundary": "activity",
                            "phase": function.__name__, "run_id": request.run_id,
                            "execution_id": request.execution_id,
                            "operation_id": request.operation_id,
                            "fencing_token": request.lease_token,
                            "execution_attempt": getattr(request, "execution_attempt", None),
                        },
                        type=code,
                        non_retryable=exc.non_retryable if isinstance(exc, ApplicationError) else True,
                    ) from None
                raise
            finally:
                heartbeat.cancel()
                await asyncio.gather(heartbeat, return_exceptions=True)

    return wrapped
