from typing import List, Dict, Any
from common.types import Event, EventType


class HarnessContextBuilder:
    def __init__(self, system_prompt: str):
        self._system_prompt = system_prompt

    def build(self, events: List[Event], max_turns: int = 20) -> List[Dict[str, Any]]:
        messages = [{"role": "system", "content": self._system_prompt}]
        filtered_events = [e for e in events if e.event_type in (EventType.USER_MESSAGE, EventType.MODEL_MESSAGE, EventType.TOOL_RESULT)]
        if len(filtered_events) > max_turns * 2:
            filtered_events = filtered_events[-max_turns * 2:]
        for event in filtered_events:
            if event.event_type == EventType.USER_MESSAGE:
                messages.append({"role": "user", "content": self._extract_user_content(event)})
            elif event.event_type == EventType.MODEL_MESSAGE:
                messages.append({"role": "assistant", "content": self._extract_model_content(event)})
            elif event.event_type == EventType.TOOL_RESULT:
                messages.append({"role": "user", "content": self._extract_tool_result(event)})
        return messages

    def _extract_user_content(self, event: Event) -> str:
        content = event.content
        if isinstance(content, dict):
            return str(event.metadata) + content.get("content", "")
        return str(content)

    def _extract_model_content(self, event: Event) -> str:
        content = event.content
        if isinstance(content, dict):
            text = content.get("text", "")
            tool_calls = content.get("tool_calls", [])
            if tool_calls:
                parts = []
                if text:
                    parts.append({"type": "text", "text": text})
                for tc in tool_calls:
                    parts.append({"type": "tool_use", "id": tc.get("id", ""), "name": tc.get("name", ""), "input": tc.get("arguments", {})})
                return parts
            return text
        return str(content)

    def _extract_tool_result(self, event: Event) -> str:
        content = event.content
        if isinstance(content, dict):
            result = content.get("result", "")
            error = content.get("error")
            if error:
                return f"Tool execution failed: {error}"
            return str(result)
        return str(content)
