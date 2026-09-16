"""Pure execution boundary shared by QQ and Web hosts (D-04)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol

from application.execution_contracts import EventSink as EventSink
from application.execution_contracts import ExecutionContextProvider as ExecutionContextProvider
from application.execution_contracts import ExecutionRequest as ExecutionRequest
from application.execution_contracts import StableExecutionFailure as StableExecutionFailure


@dataclass(frozen=True)
class ExecutionResult:
    content: str
    tool_turns: int
    memory_observations: tuple[Mapping[str, Any], ...] = ()


class ExecutionControl(Protocol):
    @property
    def deadline(self) -> datetime: ...

    def cancelled(self) -> bool: ...

    def raise_if_cancelled(self) -> None: ...

    async def heartbeat(self, phase: str) -> None: ...


class ExecutionAuditSink(Protocol):
    """Safe, non-authoritative execution observations.

    Message/Run terminal state and raw tool payloads deliberately do not cross
    this port.
    """

    async def model_step(
        self,
        execution_id: str,
        turn: int,
        content: str,
        tool_calls: tuple[Mapping[str, Any], ...],
        stop_reason: str,
        input_context: Mapping[str, Any] | None,
    ) -> None: ...

    async def tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> None: ...

    async def authorize_tool_intent(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        side_effect_class: str,
    ) -> None: ...


class NullExecutionAuditSink:
    async def model_step(
        self,
        execution_id: str,
        turn: int,
        content: str,
        tool_calls: tuple[Mapping[str, Any], ...],
        stop_reason: str,
        input_context: Mapping[str, Any] | None,
    ) -> None:
        return None

    async def tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> None:
        return None

    async def authorize_tool_intent(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        side_effect_class: str,
    ) -> None:
        raise RuntimeError("durable side-effect intent audit is unavailable")


class ExecutionAuditSinkFactory(Protocol):
    def for_execution(
        self, execution_id: str, request: ExecutionRequest
    ) -> ExecutionAuditSink: ...


class BrainActionLoop(Protocol):
    async def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
        events: EventSink,
        audit: ExecutionAuditSink,
    ) -> ExecutionResult: ...


class AgentExecutionFacade:
    """Runs the brain/action loop; hosts own all reply persistence or delivery."""

    def __init__(self, loop: BrainActionLoop):
        self._loop = loop

    async def execute(
        self,
        request: ExecutionRequest,
        control: ExecutionControl,
        events: EventSink,
        audit: ExecutionAuditSink | None = None,
    ) -> ExecutionResult:
        if control.cancelled():
            raise asyncio.CancelledError
        try:
            return await self._loop.execute(
                request, control, events, audit or NullExecutionAuditSink()
            )
        finally:
            # ActionRuntime is intentionally duck-typed at this boundary. The
            # production loop exposes its runtime for deterministic cleanup,
            # while fake loops and future implementations remain valid.
            actions = getattr(self._loop, "_actions", None)
            clear = getattr(actions, "clear_execution", None)
            if clear is not None:
                clear(request.session_id, request.execution_id)
