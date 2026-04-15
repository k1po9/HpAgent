import httpx
from typing import Callable, Awaitable, Optional
from dataclasses import dataclass
import json
from ..harness.events import StopReason


@dataclass
class ModelResponse:
    content: Optional[str]
    tool_calls: Optional[list[dict]]
    stop_reason: StopReason
    usage: Optional[dict] = None


class ModelClient:
    def __init__(self, api_key: str, base_url: Optional[str] = None, model: str = "claude-sonnet-4-20250514"):
        self.api_key = api_key
        self.base_url = base_url or "https://api.anthropic.com"
        self.model = model

    async def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream: bool = True,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ModelResponse:
        base_url = self.base_url.rstrip('/')
        
        if "anthropic" in base_url.lower():
            return await self._chat_anthropic(messages, tools, stream, on_text_delta)
        else:
            return await self._chat_openai(messages, tools, stream, on_text_delta)

    async def _chat_anthropic(self, messages: list[dict], tools: Optional[list[dict]], stream: bool,
                               on_text_delta: Optional[Callable[[str], Awaitable[None]]]) -> ModelResponse:
        url = f"{self.base_url}/v1/messages"
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        
        payload = {"model": self.model, "messages": messages, "max_tokens": 4096}
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            if stream:
                return await self._parse_stream_anthropic(response, on_text_delta)
            else:
                return self._parse_non_stream_anthropic(response.json())

    async def _parse_stream_anthropic(self, response: httpx.Response,
                                        on_text_delta: Optional[Callable[[str], Awaitable[None]]]) -> ModelResponse:
        content, tool_calls, stop_reason = "", [], StopReason.END_TURN
        
        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            
            data = line[6:]
            if data == "[DONE]":
                break
            
            try:
                event = json.loads(data)
                if event.get("type") == "content_block_delta":
                    delta = event.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        content += text
                        if on_text_delta:
                            await on_text_delta(text)
                elif event.get("type") in ["message_delta", "message"]:
                    stop_reason = StopReason(event.get("delta", {}).get("stop_reason", 
                                                                    event.get("stop_reason", "end_turn")))
            except json.JSONDecodeError:
                continue
        
        return ModelResponse(content=content, tool_calls=tool_calls if tool_calls else None, stop_reason=stop_reason)

    def _parse_non_stream_anthropic(self, result: dict) -> ModelResponse:
        content, tool_calls = "", []
        
        for block in result.get("content", []):
            if block.get("type") == "text":
                content += block.get("text", "")
            elif block.get("type") == "tool_use":
                tool_calls.append({"id": block.get("id"), "name": block.get("name"), "input": block.get("input", {})})
        
        stop_reason = StopReason(result.get("stop_reason", "end_turn"))
        return ModelResponse(content=content, tool_calls=tool_calls if tool_calls else None, stop_reason=stop_reason)

    async def _chat_openai(self, messages: list[dict], tools: Optional[list[dict]], stream: bool,
                            on_text_delta: Optional[Callable[[str], Awaitable[None]]]) -> ModelResponse:
        base_url = self.base_url.rstrip('/')
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        
        payload = {"model": self.model, "messages": messages, "temperature": 0.7}
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            
            if stream:
                return await self._parse_stream_openai(response, on_text_delta)
            else:
                return self._parse_non_stream_openai(response.json())

    async def _parse_stream_openai(self, response: httpx.Response,
                                     on_text_delta: Optional[Callable[[str], Awaitable[None]]]) -> ModelResponse:
        content, tool_calls, stop_reason = "", [], StopReason.END_TURN
        
        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            
            data = line[6:]
            if data == "[DONE]":
                break
            
            try:
                event = json.loads(data)
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
                            tool_calls.append({"id": "", "name": "", "input": {}})
                        
                        if tc.get("id"):
                            tool_calls[index]["id"] = tc["id"]
                        if tc.get("function"):
                            tool_calls[index]["name"] = tc["function"]["name"]
                            try:
                                args = tc["function"]["arguments"]
                                tool_calls[index]["input"] = json.loads(args) if isinstance(args, str) else args
                            except:
                                tool_calls[index]["input"] = {}
                
                if event.get("finish_reason") and event.get("finish_reason") != "stop":
                    stop_reason = StopReason(event.get("finish_reason"))
            except json.JSONDecodeError:
                continue
        
        return ModelResponse(content=content, tool_calls=tool_calls if tool_calls else None,
                           stop_reason=StopReason.TOOL_USE if tool_calls else stop_reason)

    def _parse_non_stream_openai(self, result: dict) -> ModelResponse:
        message = result.get("choices", [{}])[0].get("message", {})
        content = message.get("content", "")
        
        tool_calls = None
        if message.get("tool_calls"):
            tool_calls = [{"id": tc.get("id"), "name": tc.get("function", {}).get("name"),
                         "input": tc.get("function", {}).get("arguments", {})} for tc in message.get("tool_calls", [])]
        
        finish_reason = result.get("choices", [{}])[0].get("finish_reason", "stop")
        stop_reason = StopReason.TOOL_USE if tool_calls else StopReason(finish_reason) if finish_reason != "tool_calls" else StopReason.TOOL_USE
        
        return ModelResponse(content=content, tool_calls=tool_calls, stop_reason=stop_reason)
