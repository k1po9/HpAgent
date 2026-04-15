from typing import Callable, Awaitable, Optional
import time
from .registry import ToolRegistry
from ..harness.events import ExecutionEvent, EventType


class ToolRouter:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    async def route(
        self,
        tool_calls: list[dict],
        turn_index: int,
        on_event: Optional[Callable[[ExecutionEvent], Awaitable[None]]] = None,
    ) -> list[dict]:
        results = []
        for tc in tool_calls:
            tool = self.registry.get(tc["name"])
            if not tool:
                results.append(self._error_result(tc["id"], f"Tool not found: {tc['name']}"))
                continue

            if on_event:
                await on_event(ExecutionEvent(
                    type=EventType.TOOL_CALL_STARTED,
                    turn_index=turn_index,
                    timestamp=time.time(),
                    data={"tool_name": tc["name"], "call_id": tc["id"], "input": tc["input"]}
                ))

            try:
                output = await tool.execute(**tc["input"])
                results.append({
                    "type": "tool_result",
                    "tool_use_id": tc["id"],
                    "content": str(output),
                })
            except Exception as e:
                results.append(self._error_result(tc["id"], str(e)))

            if on_event:
                await on_event(ExecutionEvent(
                    type=EventType.TOOL_CALL_COMPLETED,
                    turn_index=turn_index,
                    timestamp=time.time(),
                    data={"tool_name": tc["name"], "call_id": tc["id"]}
                ))
        return results

    def _error_result(self, call_id: str, error: str) -> dict:
        return {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": f"Error: {error}",
            "is_error": True,
        }
