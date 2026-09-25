"""Task-local identity for one logical model call.

Services deliberately do not live in this ContextVar. The ResourcePool receives
governance services from the composition root; this value only carries durable
call identity across Brain layers.
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator
from uuid import NAMESPACE_URL, UUID, uuid5


@dataclass
class ModelCallContext:
    account_id: UUID
    run_id: UUID
    operation_id: str
    phase: str
    execution_attempt: int = 1
    final_response: bool = False
    call_ordinal: int = 0
    artifact_id: str | None = None
    artifact_version_id: str | None = None
    workflow_id: str | None = None
    model_call_id: UUID | None = None
    endpoint_id: str | None = None
    provider: str | None = None
    model: str | None = None
    attempt: int | None = None
    failure_logged: bool = False

    def begin_logical_call(self) -> tuple[int, UUID]:
        self.call_ordinal += 1
        identity = (
            f"hpagent:model-call:{self.account_id}:{self.run_id}:"
            f"{self.operation_id}:{self.phase}:{self.execution_attempt}:"
            f"{self.call_ordinal}"
        )
        return self.call_ordinal, uuid5(NAMESPACE_URL, identity)

    def attempt_operation_id(
        self, model_call_id: UUID, fallback_attempt: int, endpoint_id: str
    ) -> str:
        digest = hashlib.sha256(
            f"{model_call_id}:{self.execution_attempt}:{fallback_attempt}:"
            f"{endpoint_id}".encode("utf-8")
        ).hexdigest()[:32]
        return f"model:{model_call_id}:a{self.execution_attempt}:f{fallback_attempt}:{digest}"

ModelBudgetContext = ModelCallContext


_CURRENT: ContextVar[ModelCallContext | None] = ContextVar(
    "hpagent_model_call_context", default=None
)


@contextmanager
def model_budget_scope(
    account_id: UUID | str | None,
    run_id: UUID | str,
    operation_id: str,
    *,
    phase: str = "model",
    execution_attempt: int = 1,
    final_response: bool = False,
    artifact_id: str | None = None,
    artifact_version_id: str | None = None,
    workflow_id: str | None = None,
) -> Iterator[ModelCallContext]:
    """Bind one durable operation without leaking it to concurrent Runs."""
    def identity_uuid(value: object, namespace: str) -> UUID:
        try:
            return UUID(str(value))
        except (TypeError, ValueError, AttributeError):
            return uuid5(NAMESPACE_URL, f"hpagent:{namespace}:{value}")
    context = ModelCallContext(
        identity_uuid(account_id, "account"), identity_uuid(run_id, "run"),
        operation_id, phase, execution_attempt, final_response,
        artifact_id=artifact_id, artifact_version_id=artifact_version_id,
        workflow_id=workflow_id,
    )
    token = _CURRENT.set(context)
    try:
        yield context
    finally:
        _CURRENT.reset(token)


def current_model_call() -> ModelCallContext | None:
    return _CURRENT.get()


def current_model_budget() -> ModelCallContext | None:
    """Compatibility accessor; returns identity, never a budget service."""
    return current_model_call()
