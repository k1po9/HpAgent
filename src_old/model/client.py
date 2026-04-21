import httpx
from typing import Callable, Awaitable, Optional
from dataclasses import dataclass
import json
from ..execution.harness.events import StopReason, map_openai_finish_reason
from ..tools.service import ToolService
from ..core.config import ModelConfig

@dataclass
class ModelResponse:
    content: Optional[str]
    tool_calls: Optional[list[dict]]
    stop_reason: StopReason
    usage: Optional[dict] = None


class ModelClient:
    def __init__(self, model_config: ModelConfig):
        self.api_key = model_config.api_key
        self.base_url = model_config.base_url
        self.model = model_config.model

    async def generate(
        self,
        messages: list[dict],
        tool_service: ToolService,
        stream: bool = True,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ModelResponse:

        # 根据模型url进行不同接口API的访问
        base_url = self.base_url.rstrip('/')        

        return await self._chat_openai(messages, tool_service, stream, on_text_delta)


    async def _chat_openai(self, messages: list[dict], tool_service: ToolService, stream: bool,
                            on_text_delta: Optional[Callable[[str], Awaitable[None]]]) -> ModelResponse:
        base_url = self.base_url.rstrip('/')
        url = f"{base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        
        payload = {"model": self.model, "messages": messages, "temperature": 0.7}
        if tool_service:
            payload["tools"] = tool_service.list_all_tools()
        if stream:
            payload["stream"] = True

        async with httpx.AsyncClient(timeout=30.0) as client:
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
                    stop_reason = StopReason(map_openai_finish_reason(event.get("finish_reason")))
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
        finish_reason = map_openai_finish_reason(finish_reason)

        stop_reason = StopReason.TOOL_USE if tool_calls else StopReason(finish_reason) if finish_reason != "tool_calls" else StopReason.TOOL_USE
        
        return ModelResponse(content=content, tool_calls=tool_calls, stop_reason=stop_reason)
