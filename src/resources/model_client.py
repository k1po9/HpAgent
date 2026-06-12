"""
ModelClient —— 单个模型 API 的 HTTP 客户端。

支持:
  - Anthropic Messages API（非流式 + SSE 流式）
  - OpenAI Chat Completions API（非流式 + SSE 流式）

通过 api_format 配置项区分:
  - "anthropic": POST {base_url}/messages, x-api-key header, content[] 响应
  - "openai":    POST {base_url}/chat/completions, Bearer header, choices[] 响应

调用链路:
  Harness.call_model_activity → ResourcePool.generate → ModelClient.generate → httpx POST
"""
from typing import Dict, Any, Optional, List, Callable, Awaitable
import json
import logging
import re
import time

from common.types import ModelResponse, StopReason, ToolCall
from common.errors import ModelAPIError

logger = logging.getLogger("HpAgent.ModelClient")


class ModelClient:
    """单个 LLM API 的 HTTP 客户端。

    用法::

        client = ModelClient({
            "api_key": "sk-xxx",
            "base_url": "https://api.anthropic.com/v1",
            "model": "claude-sonnet-4-6",
            "api_format": "anthropic",
        })
        response = await client.generate(messages=[...], tools=[...])
    """

    _ANTHROPIC_VERSION = "2023-06-01"

    def __init__(self, config: Dict[str, Any]):
        """
        Args:
            config: {"api_key": str, "base_url": str, "model": str,
                     "api_format": str (default "anthropic"),
                     "max_tokens": int (optional, default 2048),
                     "timeout": float (optional, default 30.0)}
        """
        self.api_key = config["api_key"]
        self.base_url = config["base_url"].rstrip("/")
        self.model = config["model"]
        self.api_format = config.get("api_format", "anthropic")
        self._max_tokens = config.get("max_tokens", 2048)
        self._timeout = config.get("timeout", 30.0)
        self._extra_body = config.get("extra_body") or {}

    # ═══════════════════════════════════════════════════════════════════════════
    # 主入口
    # ═══════════════════════════════════════════════════════════════════════════

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]] = None,
        max_tokens: Optional[int] = None,
    ) -> ModelResponse:
        """调用模型生成回复。max_tokens 覆盖实例默认值。"""
        import httpx

        url = self._build_url()
        headers = self._build_headers()
        payload = self._build_payload(messages, tools, stream, max_tokens=max_tokens)

        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(url, json=payload, headers=headers)
                if response.status_code >= 400:
                    logger.error(
                        "ModelClient HTTP %d: body=%s",
                        response.status_code,
                        response.text[:1000],
                    )
                response.raise_for_status()
                if stream:
                    result = await self._parse_stream(response, on_text_delta)
                else:
                    result = self._parse_non_stream(response.json())

                elapsed_ms = (time.monotonic() - t0) * 1000
                tokens = self._extract_usage(result, response)
                tc_names = [tc.name for tc in (result.tool_calls or [])]
                if elapsed_ms > 8000:
                    logger.warning(
                        "Model call %s SLOW: latency=%.0fms tokens=%s tools=%s stop=%s",
                        self.model, elapsed_ms, tokens, tc_names or None,
                        result.stop_reason.value if result.stop_reason else "?",
                    )
                else:
                    logger.info(
                        "Model call %s latency=%.0fms tokens=%s tools=%s stop=%s",
                        self.model, elapsed_ms, tokens, tc_names or None,
                        result.stop_reason.value if result.stop_reason else "?",
                    )
                return result
            except httpx.HTTPStatusError as e:
                # 捕获响应体，确保降级日志能看到具体报错内容
                try:
                    resp_body = e.response.text[:500]
                except Exception:
                    resp_body = "(unable to read response body)"
                raise ModelAPIError(
                    reason=f"HTTP {e.response.status_code}: {resp_body}",
                    status_code=e.response.status_code,
                )
            except Exception as e:
                raise ModelAPIError(reason=f"{type(e).__name__}: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # URL / Headers / Payload
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_url(self) -> str:
        if self.api_format == "openai":
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/messages"

    def _build_headers(self) -> Dict[str, str]:
        if self.api_format == "anthropic":
            return {
                "x-api-key": self.api_key,
                "anthropic-version": self._ANTHROPIC_VERSION,
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _build_payload(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        stream: bool,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": self._convert_messages(messages) if self.api_format == "openai" else messages,
            "max_tokens": max_tokens if max_tokens is not None else self._max_tokens,
        }
        if tools:
            if self.api_format == "openai":
                payload["tools"] = self._tools_to_openai(tools)
            else:
                payload["tools"] = self._tools_to_anthropic(tools)
        if stream:
            payload["stream"] = True
        if self._extra_body:
            payload.update(self._extra_body)
        return payload

    # ═══════════════════════════════════════════════════════════════════════════
    # 消息历史格式转换（Anthropic ↔ OpenAI）
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _convert_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将 Anthropic 格式的消息历史转为 OpenAI 格式。

        Anthropic tool_use 格式:
          {"role":"assistant", "content": [{"type":"text",...}, {"type":"tool_use",...}]}
          {"role":"user", "content": "tool result text"}  # 工具结果

        OpenAI 格式:
          {"role":"assistant", "content":"...", "tool_calls": [...]}
          {"role":"tool", "tool_call_id":"...", "content":"..."}
        """
        converted: List[Dict[str, Any]] = []
        pending_ids: List[str] = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            if role == "assistant" and isinstance(content, list):
                text_parts: List[str] = []
                tool_calls: List[Dict[str, Any]] = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tc_id = block.get("id", "")
                        pending_ids.append(tc_id)
                        tool_calls.append({
                            "id": tc_id,
                            "type": "function",
                            "function": {
                                "name": block.get("name", ""),
                                "arguments": json.dumps(block.get("input", {}), ensure_ascii=False),
                            },
                        })
                new_msg: Dict[str, Any] = {"role": "assistant"}
                if text_parts:
                    new_msg["content"] = "\n".join(text_parts)
                if tool_calls:
                    new_msg["tool_calls"] = tool_calls
                if not text_parts and not tool_calls:
                    new_msg["content"] = ""
                converted.append(new_msg)

            elif role == "user" and pending_ids:
                # 判断是工具结果还是新的用户消息
                if isinstance(content, str) and content.strip():
                    # 可能是工具结果，配对最近的 tool_call
                    tc_id = pending_ids.pop(0)
                    converted.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": content,
                    })
                else:
                    # 空内容或非字符串 → 残留的 pending_ids 不再匹配，刷新
                    pending_ids.clear()
                    converted.append({"role": "user", "content": str(content) if content else ""})

            else:
                if role == "user":
                    pending_ids.clear()  # 新的 user 消息意味着上一轮 tool 序列结束
                converted.append(dict(msg))

        return converted

    # ═══════════════════════════════════════════════════════════════════════════
    # 工具格式转换
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _tools_to_anthropic(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将工具定义转为 Anthropic 格式。

        OpenAI 入: {"type": "function", "function": {"name": ..., "parameters": ...}}
        Anthropic 出: {"name": ..., "description": ..., "input_schema": ...}
        已是 Anthropic 格式则原样返回。
        """
        normalized: List[Dict[str, Any]] = []
        for tool in tools:
            if "function" in tool:
                fn = tool["function"]
                normalized.append({
                    "name": fn.get("name", ""),
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {}),
                })
            else:
                normalized.append(tool)
        return normalized

    @staticmethod
    def _tools_to_openai(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """将工具定义转为 OpenAI 格式。

        Anthropic 入: {"name": ..., "input_schema": ...}
        OpenAI 出: {"type": "function", "function": {"name": ..., "parameters": ...}}
        已是 OpenAI 格式则原样返回。
        """
        normalized: List[Dict[str, Any]] = []
        for tool in tools:
            if "function" in tool:
                normalized.append(tool)
            else:
                normalized.append({
                    "type": "function",
                    "function": {
                        "name": tool.get("name", ""),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("input_schema", {}),
                    },
                })
        return normalized

    # ═══════════════════════════════════════════════════════════════════════════
    # XML tool call 回退解析（防御性：部分模型在 content 中返回 XML 而非结构化 tool_calls）
    # ═══════════════════════════════════════════════════════════════════════════

    _TOOL_CALL_XML_RE = re.compile(
        r'<tool_call>\s*<function=(\w[\w\-]*)>(.*?)</tool_call>',
        re.DOTALL,
    )
    _PARAM_XML_RE = re.compile(
        r'<parameter=(\w+)>(.*?)</parameter>',
        re.DOTALL,
    )

    @classmethod
    def _extract_xml_tool_calls(cls, content_text: str) -> tuple[list[ToolCall], str]:
        """从文本内容中提取 XML 格式的工具调用。

        某些模型（如 mimo-v2.5）通过 content 返回 <tool_call> XML
        而非结构化 tool_calls 字段。此方法提供回退解析。

        Returns:
            (tool_calls, cleaned_text): 提取的工具调用列表和清理后的文本。
        """
        tool_calls: list[ToolCall] = []
        cleaned = content_text

        for match in cls._TOOL_CALL_XML_RE.finditer(content_text):
            tool_name = match.group(1)
            inner = match.group(2)
            arguments: dict[str, Any] = {}
            for pm in cls._PARAM_XML_RE.finditer(inner):
                key = pm.group(1)
                value = pm.group(2).strip()
                # 尝试解析 JSON 值（如数组、对象）
                try:
                    value = json.loads(value)
                except (json.JSONDecodeError, TypeError):
                    pass  # 保持字符串
                arguments[key] = value
            tool_calls.append(ToolCall(
                id=f"xml_{len(tool_calls)}",
                name=tool_name,
                arguments=arguments,
            ))

        if tool_calls:
            # 清理 XML 块，替换为空白避免留下残留字符
            cleaned = cls._TOOL_CALL_XML_RE.sub('', cleaned).strip()

        return tool_calls, cleaned

    # ═══════════════════════════════════════════════════════════════════════════
    # 非流式响应解析
    # ═══════════════════════════════════════════════════════════════════════════

    def _parse_non_stream(self, result: dict) -> ModelResponse:
        if self.api_format == "openai":
            return self._parse_openai_response(result)
        return self._parse_anthropic_response(result)

    def _parse_anthropic_response(self, result: dict) -> ModelResponse:
        """解析 Anthropic Messages API 非流式响应。

        content 是数组: [{"type": "text", "text": "..."}, {"type": "tool_use", ...}]
        """
        content_text = ""
        tool_calls: list[ToolCall] = []

        content_list = result.get("content", [])
        if content_list and isinstance(content_list, list):
            for item in content_list:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text":
                    content_text = item.get("text", "")
                elif item.get("type") == "tool_use":
                    tool_calls.append(ToolCall(
                        id=item.get("id", ""),
                        name=item.get("name", ""),
                        arguments=item.get("input", {}),
                    ))

        stop_reason = self._map_anthropic_stop_reason(
            result.get("stop_reason", "end")
        )
        if tool_calls and stop_reason == StopReason.END_TURN:
            stop_reason = StopReason.TOOL_USE

        # XML 回退：部分模型返回 <tool_call> 在 content 中
        if not tool_calls and content_text and '<tool_call>' in content_text:
            xml_tool_calls, cleaned_text = self._extract_xml_tool_calls(content_text)
            if xml_tool_calls:
                logger.warning(
                    "Model %s: extracted %d tool calls from XML in content, "
                    "stop_reason=%s → TOOL_USE",
                    self.model, len(xml_tool_calls),
                    result.get("stop_reason", "?"),
                )
                tool_calls = xml_tool_calls
                content_text = cleaned_text
                stop_reason = StopReason.TOOL_USE

        return ModelResponse(
            content=content_text or None,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=stop_reason,
        )

    def _parse_openai_response(self, result: dict) -> ModelResponse:
        """解析 OpenAI Chat Completions 非流式响应。

        choices: [{"message": {"content": "...", "tool_calls": [...]}, "finish_reason": "stop"}]
        """
        choice = (result.get("choices") or [{}])[0]
        message = choice.get("message", {})
        content_text = message.get("content", "") or ""

        tool_calls: list[ToolCall] = []
        for tc in message.get("tool_calls") or []:
            fn = tc.get("function", {})
            try:
                args = fn.get("arguments", "{}")
                arguments = json.loads(args) if isinstance(args, str) else args
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(
                id=tc.get("id", ""),
                name=fn.get("name", ""),
                arguments=arguments,
            ))

        stop_reason = self._map_openai_finish_reason(
            choice.get("finish_reason", "stop")
        )
        if tool_calls and stop_reason == StopReason.END_TURN:
            stop_reason = StopReason.TOOL_USE

        # XML 回退：部分模型返回 <tool_call> 在 content 中
        if not tool_calls and content_text and '<tool_call>' in content_text:
            xml_tool_calls, cleaned_text = self._extract_xml_tool_calls(content_text)
            if xml_tool_calls:
                logger.warning(
                    "Model %s: extracted %d tool calls from XML in content, "
                    "stop_reason=%s → TOOL_USE",
                    self.model, len(xml_tool_calls),
                    choice.get("finish_reason", "?"),
                )
                tool_calls = xml_tool_calls
                content_text = cleaned_text
                stop_reason = StopReason.TOOL_USE

        return ModelResponse(
            content=content_text or None,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=stop_reason,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 流式响应解析
    # ═══════════════════════════════════════════════════════════════════════════

    async def _parse_stream(
        self,
        response: Any,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]],
    ) -> ModelResponse:
        if self.api_format == "openai":
            return await self._parse_openai_stream(response, on_text_delta)
        return await self._parse_anthropic_stream(response, on_text_delta)

    async def _parse_anthropic_stream(
        self,
        response: Any,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]],
    ) -> ModelResponse:
        """解析 Anthropic SSE 流式响应。

        事件类型:
          - message_start:     {"type": "message_start", "message": {...}}
          - content_block_start: {"type": "content_block_start", "content_block": {"type": "tool_use", ...}}
          - content_block_delta: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "..."}}
          - message_delta:     {"type": "message_delta", "delta": {"stop_reason": "..."}}
          - message_stop:      {"type": "message_stop"}
        """
        content = ""
        tool_calls: list[ToolCall] = []
        stop_reason = StopReason.END_TURN

        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if not data:
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "content_block_start":
                cb = event.get("content_block", {})
                if cb.get("type") == "tool_use":
                    tool_calls.append(ToolCall(
                        id=cb.get("id", ""),
                        name=cb.get("name", ""),
                        arguments={},
                    ))

            elif event_type == "content_block_delta":
                delta = event.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    content += text
                    if on_text_delta:
                        await on_text_delta(text)
                elif delta.get("type") == "input_json_delta":
                    partial = delta.get("partial_json", "")
                    if tool_calls:
                        tool_calls[-1].arguments = (
                            tool_calls[-1].arguments or {}
                        )

            elif event_type == "message_delta":
                sr = event.get("delta", {}).get("stop_reason", "end")
                stop_reason = self._map_anthropic_stop_reason(sr)

        if tool_calls:
            stop_reason = StopReason.TOOL_USE
        return ModelResponse(
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=stop_reason,
        )

    async def _parse_openai_stream(
        self,
        response: Any,
        on_text_delta: Optional[Callable[[str], Awaitable[None]]],
    ) -> ModelResponse:
        """解析 OpenAI SSE 流式响应。

        data: {"choices": [{"delta": {"content": "...", "tool_calls": [...]}, "finish_reason": "..."}]}
        """
        content = ""
        tool_calls: list[ToolCall] = []
        stop_reason = StopReason.END_TURN

        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data = line[6:].strip()
            if not data or data == "[DONE]":
                break
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue

            choice = (event.get("choices") or [{}])[0]
            delta = choice.get("delta", {})

            if delta.get("content"):
                text = delta["content"]
                content += text
                if on_text_delta:
                    await on_text_delta(text)

            if delta.get("tool_calls"):
                for tc in delta["tool_calls"]:
                    index = tc.get("index", 0)
                    while len(tool_calls) <= index:
                        tool_calls.append(ToolCall(id="", name="", arguments={}))
                    if tc.get("id"):
                        tool_calls[index].id = tc["id"]
                    if tc.get("function"):
                        tool_calls[index].name = tc["function"].get("name", "")
                        try:
                            args = tc["function"].get("arguments", "{}")
                            tool_calls[index].arguments = (
                                json.loads(args) if isinstance(args, str) else args
                            )
                        except (json.JSONDecodeError, TypeError):
                            pass

            finish_reason = choice.get("finish_reason")
            if finish_reason and finish_reason != "stop":
                stop_reason = self._map_openai_finish_reason(finish_reason)

        if tool_calls:
            stop_reason = StopReason.TOOL_USE
        return ModelResponse(
            content=content if content else None,
            tool_calls=tool_calls if tool_calls else None,
            stop_reason=stop_reason,
        )

    # ═══════════════════════════════════════════════════════════════════════════
    # 用量提取
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _extract_usage(result: "ModelResponse", raw_response: Any) -> dict | None:
        """从原始 HTTP 响应提取 token 用量（兼容 Anthropic / OpenAI 格式）。"""
        try:
            body = raw_response.json() if hasattr(raw_response, "json") else {}
        except Exception:
            return None
        if not body:
            return None
        usage = body.get("usage")
        if not usage:
            return None
        # Anthropic 格式: {"input_tokens": N, "output_tokens": N}
        if "input_tokens" in usage:
            return {"in": usage.get("input_tokens"), "out": usage.get("output_tokens")}
        # OpenAI 格式: {"prompt_tokens": N, "completion_tokens": N, "total_tokens": N}
        if "prompt_tokens" in usage:
            return {"in": usage.get("prompt_tokens"), "out": usage.get("completion_tokens")}
        return None

    # ═══════════════════════════════════════════════════════════════════════════
    # Stop reason 映射
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _map_anthropic_stop_reason(reason: str) -> StopReason:
        mapping = {
            "end": StopReason.END_TURN,
            "end_turn": StopReason.END_TURN,
            "max_tokens": StopReason.MAX_TOKENS,
            "stop": StopReason.END_TURN,
            "stop_sequence": StopReason.END_TURN,
            "tool_use": StopReason.TOOL_USE,
        }
        return mapping.get(reason, StopReason.ERROR)

    @staticmethod
    def _map_openai_finish_reason(reason: str) -> StopReason:
        mapping = {
            "stop": StopReason.END_TURN,
            "length": StopReason.MAX_TOKENS,
            "content_filter": StopReason.REFUSAL,
            "tool_calls": StopReason.TOOL_USE,
            "function_call": StopReason.TOOL_USE,
        }
        return mapping.get(reason, StopReason.ERROR)
