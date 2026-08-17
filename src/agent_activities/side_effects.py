"""Recovery contracts for tool side effects executed by Activities."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class ReconcileOutcome(StrEnum):
    CONFIRMED_COMPLETED = "confirmed_completed"
    CONFIRMED_NOT_EXECUTED = "confirmed_not_executed"
    UNCERTAIN = "uncertain"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class ReconcileResult:
    outcome: ReconcileOutcome
    result: Any | None = None


class ToolSideEffectReconciler(Protocol):
    async def reconcile(
        self,
        *,
        operation_id: str,
        tool_name: str,
        arguments_hash: str,
        intent: dict[str, Any],
    ) -> ReconcileResult: ...


class UnsupportedToolSideEffectReconciler:
    async def reconcile(self, **_: Any) -> ReconcileResult:
        return ReconcileResult(ReconcileOutcome.UNSUPPORTED)


class FaultInjector(Protocol):
    def hit(self, point: str) -> None: ...


class NoopFaultInjector:
    def hit(self, point: str) -> None:
        return None


def normalize_side_effect_class(value: str) -> str:
    """Map legacy registry values onto the durable retry policy classes."""
    if value in {"none", "read_only"}:
        return "read_only"
    if value in {"workspace_write", "idempotent_write"}:
        return "idempotent_write"
    if value in {"external_write", "non_idempotent_write"}:
        return "non_idempotent_write"
    return "unknown"
