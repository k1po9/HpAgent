"""Trusted request scope; paid attempts use existing Run and cumulative Work ledgers."""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from resources.run_budget import RunBudgetService


class RetrievalUsageUnknown(RuntimeError):
    code = 'usage_unknown'


@dataclass(frozen=True)
class RetrievalBudget:
    service: RunBudgetService
    run_id: UUID
    operation_id: str

    async def before_request(self, operation: str) -> None:
        # Reserve commits before any send. A replay never buys permission to send again.
        key = f'{self.operation_id}:paid:{operation}:v1'
        await consume_attempt(self.service, self.run_id, key)


async def consume_attempt(service, run_id, key):
    """One conservative tool/request charge; replay cannot authorize another send."""
    result = await asyncio.to_thread(service.reserve, run_id, key, {'tool_calls': 1},
                                     enforce_limits=True)
    if result.replayed:
        raise RetrievalUsageUnknown('usage_unknown')
    # Charge before dispatch, including failure or cancellation with unknown outcome.
    await asyncio.to_thread(service.settle, run_id, key, {'tool_calls': 1}, 'estimated')


_current: ContextVar[RetrievalBudget | None] = ContextVar('retrieval_budget', default=None)


@contextmanager
def retrieval_budget_scope(service, run_id, operation_id):
    token = _current.set(RetrievalBudget(service, run_id, operation_id))
    try:
        yield
    finally:
        _current.reset(token)


async def paid_before_request(operation: str) -> None:
    context = _current.get()
    if context is None:
        raise PermissionError('trusted_retrieval_budget_required')
    await context.before_request(operation)
