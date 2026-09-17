"""Shared execution DTOs, errors and ports for canonical capability adapters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence


class ExecutionContextProvider(Protocol):
    async def recall_long_term(self, recall_query: str) -> Sequence[Any]: ...

    def compose(self, memories: Sequence[Any]) -> tuple[dict[str, Any], ...]: ...


@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: str
    account_id: str
    conversation_id: str
    session_id: str
    user_content: str
    context: tuple[dict[str, str], ...]
    trigger_message_id: str | None = None
    interaction_profile: str = "web_chat"
    metadata: Mapping[str, Any] | None = None
    context_provider: ExecutionContextProvider | None = None
    group_context_text: str = ""
    sender_name: str = ""


class StableExecutionFailure(RuntimeError):
    """Safe failure classification passed through the Activity boundary."""

    def __init__(self, code: str, safe_message: str = "执行未能完成。") -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class EventSink(Protocol):
    async def progress(self, phase: str, summary: str) -> None: ...

    async def trace_start(
        self,
        node_id: str,
        parent_id: str | None,
        name: str,
        node_type: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...

    async def trace_end(
        self,
        node_id: str,
        status: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> None: ...
