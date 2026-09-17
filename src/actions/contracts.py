"""Action requests and results shared with Brain and durable Activities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol


@dataclass(frozen=True)
class ActionRequest:
    """A tool action requested by the brain."""

    id: str
    name: str
    arguments: Dict[str, Any]

    @classmethod
    def from_tool_call(cls, tool_call: Any) -> "ActionRequest":
        return cls(
            id=str(getattr(tool_call, "id", "")),
            name=str(getattr(tool_call, "name", "")),
            arguments=dict(getattr(tool_call, "arguments", {}) or {}),
        )

    def to_assistant_tool_call(self) -> Dict[str, Any]:
        return {"name": self.name, "arguments": self.arguments}

    def to_model_event_tool_call(self, raw_tool_call: Any = None) -> Dict[str, Any]:
        if raw_tool_call is not None and hasattr(raw_tool_call, "to_dict"):
            return raw_tool_call.to_dict()
        return {"id": self.id, "name": self.name, "arguments": self.arguments}


@dataclass(frozen=True)
class ActionResult:
    """Result returned by the action runtime for one action request."""

    request: ActionRequest
    success: bool | None = None
    output: Any = None
    summary: Any = None
    error: Any = None
    metadata: Any = None
    raw: Optional[Dict[str, Any]] = None

    @classmethod
    def from_runtime_result(
        cls,
        request: ActionRequest,
        result: Dict[str, Any],
    ) -> "ActionResult":
        return cls(
            request=request,
            success=result.get("success"),
            output=result.get("output"),
            summary=result.get("summary"),
            error=result.get("error"),
            metadata=result.get("metadata"),
            raw=result,
        )

    @property
    def display_result(self) -> Any:
        return self.summary if self.summary is not None else self.output

    @property
    def failed(self) -> bool:
        """Whether the tool reported a semantic execution failure."""
        if self.success is not None:
            return not self.success
        return self.error is not None


class ActionCapability(Protocol):
    """Stable tool capability consumed by durable Agent Activities."""

    def reset_execution(self, resource_key: str, execution_id: str) -> None: ...

    async def select_tools(
        self,
        *,
        resource_key: str,
        execution_id: str,
        user_content: str,
        group_context_text: str = "",
    ) -> list[dict[str, Any]]: ...

    def side_effect_class(self, resource_key: str, tool_name: str) -> str: ...

    def budget_reservation(self, resource_key: str, tool_name: str) -> dict[str, int]: ...

    async def execute_request(
        self,
        request: ActionRequest,
        *,
        resource_key: str,
        execution_id: str,
        user_query: str = "",
        idempotency_key: str = "",
    ) -> ActionResult: ...

    def clear_execution(self, resource_key: str, execution_id: str) -> None: ...
