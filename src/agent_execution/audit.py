"""Safe, best-effort audit adapter for the shared execution kernel."""
from __future__ import annotations

import logging
from typing import Any, Mapping

from .facade import ExecutionAuditSink, ExecutionRequest

logger = logging.getLogger("HpAgent.ExecutionAudit")


class LoggingExecutionAuditSinkFactory:
    """Emit metadata-only observations without becoming a truth source."""

    def for_execution(
        self, execution_id: str, request: ExecutionRequest
    ) -> ExecutionAuditSink:
        if execution_id != request.execution_id:
            raise ValueError("audit execution identity mismatch")
        return LoggingExecutionAuditSink(execution_id)


class LoggingExecutionAuditSink:
    def __init__(self, execution_id: str) -> None:
        self._execution_id = execution_id

    async def model_step(
        self,
        execution_id: str,
        turn: int,
        content: str,
        tool_calls: tuple[Mapping[str, Any], ...],
        stop_reason: str,
        input_context: Mapping[str, Any] | None,
    ) -> None:
        self._check_identity(execution_id)
        logger.debug(
            "execution model step execution_id=%s turn=%d tools=%s",
            execution_id,
            turn,
            ",".join(str(item.get("name", "")) for item in tool_calls),
        )

    async def tool_result(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        result: Any,
    ) -> None:
        self._check_identity(execution_id)
        logger.debug(
            "execution tool result execution_id=%s tool_call_id=%s tool=%s failed=%s",
            execution_id,
            tool_call_id,
            tool_name,
            getattr(result, "error", None) is not None,
        )

    async def authorize_tool_intent(
        self,
        execution_id: str,
        tool_call_id: str,
        tool_name: str,
        side_effect_class: str,
    ) -> None:
        self._check_identity(execution_id)
        # Logs are not durable authorization evidence. Web therefore fails
        # closed for side-effecting/unknown tools until a durable adapter is
        # configured.
        raise RuntimeError("durable side-effect intent audit is unavailable")

    def _check_identity(self, execution_id: str) -> None:
        if execution_id != self._execution_id:
            raise ValueError("cross-execution audit write rejected")
