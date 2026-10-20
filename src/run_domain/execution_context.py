"""Execution identity propagated through tasks and short transaction calls."""

from contextlib import contextmanager
from contextvars import ContextVar

execution_fence: ContextVar[tuple[str, str, str, int] | None] = ContextVar(
    "execution_fence", default=None
)


@contextmanager
def fence_scope(account_id: str, run_id: str, token: int, execution_id: str):
    reset = execution_fence.set((account_id, run_id, execution_id, token))
    try:
        yield
    finally:
        execution_fence.reset(reset)
