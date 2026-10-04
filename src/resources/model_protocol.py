"""Serialize internal/OpenAI/Anthropic transcripts without guessing tool results."""
from __future__ import annotations

import json
from typing import Any

from .model_failures import ModelRequestInvalid


def _call(value: dict[str, Any]) -> dict[str, Any]:
    function = value.get("function", value)
    if not isinstance(function, dict):
        raise ModelRequestInvalid("invalid function object")
    arguments = function.get("arguments", value.get("input", {}))
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError as exc:
            raise ModelRequestInvalid("invalid function arguments") from exc
    if (not isinstance(arguments, dict) or not isinstance(value.get("id"), str)
            or not value["id"] or not isinstance(function.get("name"), str)
            or not function["name"]):
        raise ModelRequestInvalid("invalid tool call")
    try:
        encoded = json.dumps(arguments, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ModelRequestInvalid("non-JSON function arguments") from exc
    return {"id": value["id"], "type": "function",
            "function": {"name": function["name"], "arguments": encoded}}


def openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    pending: set[str] = set()

    def append(message: dict[str, Any]) -> None:
        role = message.get("role")
        if role == "tool":
            identity = message.get("tool_call_id")
            if not isinstance(identity, str) or identity not in pending:
                raise ModelRequestInvalid("orphan or duplicate tool result")
            pending.remove(identity)
            result.append(message)
            return
        if pending:
            raise ModelRequestInvalid("missing tool results before next message")
        calls = message.get("tool_calls") or []
        if calls:
            ids = [item["id"] for item in calls]
            if len(set(ids)) != len(ids):
                raise ModelRequestInvalid("duplicate tool call IDs")
            pending.update(ids)
        result.append(message)

    for message in messages:
        if not isinstance(message, dict):
            raise ModelRequestInvalid("invalid message object")
        role, content = message.get("role"), message.get("content", "")
        if role not in {"system", "developer", "user", "assistant", "tool"}:
            raise ModelRequestInvalid("invalid message role")
        if role == "assistant":
            raw_calls = message.get("tool_calls") or []
            if not isinstance(raw_calls, list):
                raise ModelRequestInvalid("invalid tool calls list")
            calls = [_call(item) for item in raw_calls if isinstance(item, dict)]
            if len(calls) != len(raw_calls):
                raise ModelRequestInvalid("invalid tool call object")
            if isinstance(content, list):
                texts = []
                for block in content:
                    if not isinstance(block, dict):
                        raise ModelRequestInvalid("invalid assistant content block")
                    if block.get("type") == "tool_use":
                        calls.append(_call(block))
                    elif block.get("type") == "text":
                        text = block.get("text", "")
                        if not isinstance(text, str):
                            raise ModelRequestInvalid("invalid assistant text")
                        texts.append(text)
                    else:
                        raise ModelRequestInvalid("unsupported assistant content block")
                content = "\n".join(texts)
            if content is not None and not isinstance(content, str):
                raise ModelRequestInvalid("invalid assistant content")
            output = {"role": role, "content": content or ""}
            if calls:
                output["tool_calls"] = calls
            append(output)
        elif role == "user" and isinstance(content, list):
            ordinary = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    append({"role": "tool", "tool_call_id": block.get("tool_use_id"),
                            "content": block.get("content", "")})
                else:
                    ordinary.append(block)
            if ordinary:
                append({"role": "user", "content": ordinary})
        elif role == "tool":
            append({"role": role, "tool_call_id": message.get("tool_call_id"), "content": content})
        else:
            append({"role": role, "content": content})
    if pending:
        raise ModelRequestInvalid("incomplete tool round trip")
    return result


def anthropic_messages(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    normalized = openai_messages(messages)
    result: list[dict[str, Any]] = []
    systems = []
    for message in normalized:
        role, content = message["role"], message.get("content") or ""
        if role in {"system", "developer"}:
            if not isinstance(content, str):
                raise ModelRequestInvalid("invalid system content")
            systems.append(content)
            continue
        if role == "assistant":
            blocks = [{"type": "text", "text": content}] if content else []
            blocks.extend({"type": "tool_use", "id": item["id"],
                           "name": item["function"]["name"],
                           "input": json.loads(item["function"]["arguments"])}
                          for item in message.get("tool_calls", []))
        elif role == "tool":
            role = "user"
            blocks = [{"type": "tool_result", "tool_use_id": message["tool_call_id"],
                       "content": content}]
        else:
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        if result and result[-1]["role"] == role:
            result[-1]["content"].extend(blocks)
        else:
            result.append({"role": role, "content": blocks})
    return result, "\n\n".join(systems)
