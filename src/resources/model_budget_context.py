"""Task-local Run budget identity for model provider attempts."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


@dataclass
class ModelBudgetContext:
    service: Any
    run_id: str
    operation_id: str
    execution_attempt: int = 1
    final_response: bool = False
    invocations: int = 0

    def next_operation_id(self, attempt: int, endpoint_id: str) -> str:
        self.invocations += 1
        endpoint_hash = hashlib.sha256(endpoint_id.encode("utf-8")).hexdigest()[:12]
        prefix = self.operation_id[:140]
        return (
            f"{prefix}:model:a{self.execution_attempt}:i{self.invocations}:"
            f"f{attempt}:{endpoint_hash}"
        )


_CURRENT: ContextVar[ModelBudgetContext | None] = ContextVar(
    "hpagent_model_budget_context", default=None
)


@contextmanager
def model_budget_scope(
    service: Any,
    run_id: str,
    operation_id: str,
    *,
    execution_attempt: int = 1,
    final_response: bool = False,
) -> Iterator[None]:
    """Bind one durable operation without leaking it to concurrent Runs."""
    token = _CURRENT.set(ModelBudgetContext(
        service, str(run_id), operation_id, execution_attempt, final_response
    ))
    try:
        yield
    finally:
        _CURRENT.reset(token)


def current_model_budget() -> ModelBudgetContext | None:
    return _CURRENT.get()
