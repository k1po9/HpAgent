from typing import Dict, Any, Optional, List, Callable, Awaitable
import httpx
import json
from common.types import ModelResponse, StopReason, ToolCall
from common.errors import ModelAPIError


class ModelClient:
    def __init__(self, config: Dict[str, Any]):
        self.api_key = config.get("api_key", "")
        self.base_url = config.get("base_url", "https://api.openai.com/v1").rstrip("/")
        self.model = config.get("model", "gpt-4o-mini")
        self._tools: List[Dict[str, Any]] = []

    def set_tools(self, tools: List[Dict[str, Any]]) -> None:
        self._tools = tools

    async def generate(self, messages: List[Dict[str, Any]], tools: Optional[List[Dict[str, Any]]] = None, stream: bool = False, on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None) -> ModelResponse:
        url = f"{self.base_url}/messages"
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        payload = {"model": self.model, "messages": messages, "max_tokens": 2048}
        effective_tools = tools if tools is not None else self._tools
        if effective_tools:
            payload["tools"] = effective_tools
        if stream:
            payload["stream"] = True
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                if stream:
                    return await self._parse_stream(response, on_text_delta)
                else:
                    return self._parse_non_stream(response.json())
            except httpx.HTTPStatusError as e:
                raise ModelAPIError(reason=str(e), status_code=e.response.status_code)
            except Exception as e:
                raise ModelAPIError(reason=str(e))

    async def _parse_stream(self, response: httpx.Response, on_text_delta: Optional[Callable[[str], Awaitable[None]]]) -> ModelResponse:
        content = ""
        tool_calls: List[ToolCall] = []
        stop_reason = StopReason.END_TURN
        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if not data or data == "[DONE]":
                break
            try:
                event = json.loads(data).get("choices", [{}])[0]
                delta = event.get("delta", {})
                if delta.get("content"):
                    text = delta.get("content")
                    content += text
                    if on_text_delta:
                        await on_text_delta(text)
                if delta.get("tool_calls"):
                    for tc in delta.get("tool_calls", []):
                        index = tc.get("index", 0)
                        while len(tool_calls) <= index:
                            tool_calls.append(ToolCall(id="", name="", arguments={}))
                        if tc.get("id"):
                            tool_calls[index].id = tc["id"]
                        if tc.get("function"):
                            tool_calls[index].name = tc["function"]["name"]
                            try:
                                args = tc["function"]["arguments"]
                                tool_calls[index].arguments = json.loads(args) if isinstance(args, str) else args
                            except:
                                tool_calls[index].arguments = {}
                finish_reason = event.get("finish_reason")
                if finish_reason and finish_reason != "stop":
                    stop_reason = self._map_finish_reason(finish_reason)
            except json.JSONDecodeError:
                continue
        return ModelResponse(content=content if content else None, tool_calls=tool_calls if tool_calls else None, stop_reason=StopReason.TOOL_USE if tool_calls else stop_reason)

    def _parse_non_stream(self, result: dict) -> ModelResponse:
        content_text = ""
        tool_calls = None
        
        content_list = result.get("content", [])
        if content_list and isinstance(content_list, list):
            for item in content_list:
                if isinstance(item, dict) and item.get("type") == "text":
                    content_text = item.get("text", "")
                    break
        
        stop_reason_str = result.get("stop_reason", "end")
        stop_reason = self._map_anthropic_stop_reason(stop_reason_str)
        
        return ModelResponse(content=content_text or None, tool_calls=tool_calls, stop_reason=stop_reason)

    def _map_anthropic_stop_reason(self, reason: str) -> StopReason:
        mapping = {
            "end": StopReason.END_TURN,
            "max_tokens": StopReason.MAX_TOKENS,
            "stop": StopReason.END_TURN,
        }
        return mapping.get(reason, StopReason.ERROR)

    def _map_finish_reason(self, reason: str) -> StopReason:
        mapping = {"stop": StopReason.END_TURN, "length": StopReason.MAX_TOKENS, "content_filter": StopReason.REFUSAL, "function_call": StopReason.TOOL_USE}
        return mapping.get(reason, StopReason.ERROR)
