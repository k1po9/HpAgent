"""Brain model-step decision contract."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol

from actions.contracts import ActionRequest


@dataclass(frozen=True)
class BrainDecision:
    """A brain decision for one model step."""

    content: str
    action_requests: List[ActionRequest]
    stop_reason: str
    input_context: Dict[str, Any]
    raw_response: Any = None
    raw_tool_calls: Optional[List[Any]] = None

    @property
    def has_actions(self) -> bool:
        return bool(self.action_requests)

    def model_event_tool_calls(self) -> List[Dict[str, Any]]:
        raw_calls = self.raw_tool_calls or []
        return [
            request.to_model_event_tool_call(raw_calls[idx] if idx < len(raw_calls) else None)
            for idx, request in enumerate(self.action_requests)
        ]


class BrainCapability(Protocol):
    """Model-facing operations required by durable Agent Activities."""

    async def rewrite_recall_query(
        self, *, user_content: str, group_context_text: str = "", sender_name: str = ""
    ) -> tuple[str, Optional[list[dict[str, str]]]]: ...

    async def generate_chat_decision(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        channel_overrides: Optional[dict[str, Any]] = None,
    ) -> BrainDecision: ...

    async def generate_final_decision(
        self,
        *,
        messages: list[dict[str, Any]],
        channel_overrides: Optional[dict[str, Any]] = None,
    ) -> BrainDecision: ...
