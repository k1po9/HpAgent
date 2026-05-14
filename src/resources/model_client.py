"""
ModelClient —— 单个模型 API 的 HTTP 客户端。

支持:
  - Anthropic Messages API 的非流式（POST → JSON response）调用
  - OpenAI 兼容的流式（SSE）调用，通过 on_text_delta 回调逐 token 输出

调用链路:
  Harness.call_model_activity → ResourcePool.generate → ModelClient.generate → httpx POST

当前实现主要针对 Anthropic 兼容 API（content 为数组格式）。
"""
from typing import Dict, Any, Optional, List, Callable, Awaitable
import json
from common.types import ModelResponse, StopReason, ToolCall
from common.errors import ModelAPIError


class ModelClient:
    """单个 LLM API 的 HTTP 客户端。

    用法::

        client = ModelClient({
            "api_key": "sk-xxx",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-sonnet-4-6",
        })
        response = await client.generate(messages=[...], tools=[...])
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: {"api_key": str, "base_url": str, "model": str,
                     "max_tokens": int (optional, default 2048),
                     "timeout": float (optional, default 30.0)}
        """
        self.api_key = config["api_key"]
        self.base_url = config["base_url"].rstrip("/")
        self.model = config["model"]
        self._max_tokens = config.get("max_tokens", 2048)
        self._timeout = config.get("timeout", 30.0)
        self._tools: List[Dict[str, Any]] = []

    def set_tools(self, tools: List[Dict[str, Any]]) -> None:
        """注册工具定义列表（会被添加到每次请求的 payload 中）。"""
        self._tools = tools

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> ModelResponse:
        """调用模型生成回复。

        Args:
            messages: LLM 标准 messages 列表。
            tools: 工具定义列表（None 表示不带工具）。
            stream: True 启用 SSE 流式返回。
            on_text_delta: 流式模式下每收到一段文本时的回调。

        Returns:
            ModelResponse（含 content / tool_calls / stop_reason / usage）。

        Raises:
            ModelAPIError: HTTP 错误或网络异常。
        """
        import httpx

        url = f"{self.base_url}/messages"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self._max_tokens,
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream"] = True

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                if stream:
                    return await self._parse_stream(response, on_text_delta)
                else:
                    return self._parse_non_stream(response.json())
            except httpx.HTTPStatusError as e:
                raise ModelAPIError(
                    reason=str(e), status_code=e.response.status_code
                )
            except Exception as e:
                raise ModelAPIError(reason=str(e))

    async def _parse_stream(
        self,
        response: Any,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]],
    ) -> ModelResponse:
        """解析 SSE 流式响应（OpenAI 兼容格式）。

        逐行读取 data: {...} 事件，累积 content 和 tool_calls。
        """
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

                # 文本增量
                if delta.get("content"):
                    text = delta.get("content")
                    content += text
                    if on_text_delta:
                        await on_text_delta(text)

                # 工具调用增量（流式累积）
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
                                tool_calls[index].arguments = (
                                    json.loads(args) if isinstance(args, str) else args
                                )
                            except Exception:
                                tool_calls[index].arguments = {}

                # 停止原因
                finish_reason = event.get("finish_reason")
                if finish_reason and finish_reason != "stop":
                    stop_reason = self._map_finish_reason(finish_reason)
            except json.JSONDecodeError:
                continue

        # 有 tool_calls 时 stop_reason 强制为 TOOL_USE
        return ModelResponse(
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=StopReason.TOOL_USE if tool_calls else stop_reason,
        )

    def _parse_non_stream(self, result: dict) -> ModelResponse:
        """解析非流式 Anthropic 兼容响应。

        Anthropic API 的 content 是数组:
          [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]
        """
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

        return ModelResponse(
            content=content_text or None,
            tool_calls=tool_calls,
            stop_reason=stop_reason,
        )

    def _map_anthropic_stop_reason(self, reason: str) -> StopReason:
        """Anthropic API stop_reason → StopReason 枚举。"""
        mapping = {
            "end": StopReason.END_TURN,
            "max_tokens": StopReason.MAX_TOKENS,
            "stop": StopReason.END_TURN,
        }
        return mapping.get(reason, StopReason.ERROR)

    def _map_finish_reason(self, reason: str) -> StopReason:
        """OpenAI API finish_reason → StopReason 枚举。"""
        mapping = {
            "stop": StopReason.END_TURN,
            "length": StopReason.MAX_TOKENS,
            "content_filter": StopReason.REFUSAL,
            "function_call": StopReason.TOOL_USE,
        }
        return mapping.get(reason, StopReason.ERROR)
