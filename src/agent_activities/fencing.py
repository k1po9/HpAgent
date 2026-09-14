"""Execution fence propagated into short data-plane transactions and threads."""

from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps

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


def fenced_activity(function):
    @wraps(function)
    async def wrapped(self, request):
        with fence_scope(request.account_id, request.run_id, request.lease_token):
            try:
                return await function(self, request)
            except Exception as exc:
                if getattr(exc, "code", None) == "stale_fencing_token":
                    raise ApplicationError(str(exc), type=exc.code, non_retryable=True) from exc
                raise

    return wrapped
