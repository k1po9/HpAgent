"""Brain engine for model-facing reasoning steps.

This module is intentionally thin: it owns model calls and model-input
auditing, while orchestration, action execution, and reply delivery stay
outside the brain boundary.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from common.token_counter import estimate_messages_tokens
from agent.protocol import ActionRequest, BrainDecision

logger = logging.getLogger(__name__)


class BrainEngine:
    """Model-facing brain boundary.

    The brain does not send messages and does not execute tools. It only turns
    context into model responses or small brain-side rewrites used by recall.
    """

    def __init__(self, *, resource_pool: Any = None, prompts: Any = None) -> None:
        self._model = None
        if resource_pool is not None:
            self._model = getattr(resource_pool, "model", None) or resource_pool
        self._prompts = prompts

    async def rewrite_recall_query(
        self,
        *,
        user_content: str,
        group_context_text: str = "",
        sender_name: str = "",
    ) -> tuple[str, Optional[list[dict[str, str]]]]:
        """Rewrite a user turn for memory recall, returning the audit context."""

        prompt_template = ""
        if self._prompts is not None:
            try:
                prompt_template = self._prompts.get_system("hyde_rewrite")
            except Exception:
                prompt_template = ""

        if not prompt_template or self._model is None:
            return user_content, None

        parts: list[str] = []
        if group_context_text.strip():
            parts.append(f"群聊近期对话：\n{group_context_text}")
        if sender_name:
            parts.append(f"当前说话人：{sender_name}")
        parts.append(f"用户问题：{user_content}")
        user_prompt = "\n\n".join(parts)
        context = [
            {"role": "system", "content": prompt_template},
            {"role": "user", "content": user_prompt},
        ]

        try:
            response = await self._model.generate(
                model_selector="fast",
                messages=context,
                stream=False,
            )
            rewritten = ""
            if hasattr(response, "content"):
                rewritten = str(response.content or "").strip()
            else:
                rewritten = str(response or "").strip()
            if rewritten and len(rewritten) >= 2:
                return rewritten, context
        except Exception as exc:
            logger.warning("HyDE recall query rewrite failed: %s", exc)

        return user_content, context

    async def generate_chat(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        channel_overrides: Optional[dict[str, Any]] = None,
    ) -> Any:
        if self._model is None:
            raise RuntimeError("BrainEngine requires resource_pool.model")

        overrides = channel_overrides or {}
        return await self._model.generate(
            model_selector="chat",
            messages=messages,
            tools=tools,
            stream=overrides.get("stream", False),
            max_tokens=overrides.get("max_tokens"),
            latency_budget=overrides.get("timeout"),
        )


    async def generate_chat_decision(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        channel_overrides: Optional[dict[str, Any]] = None,
    ) -> BrainDecision:
        response = await self.generate_chat(
            messages=messages,
            tools=tools,
            channel_overrides=channel_overrides,
        )
        return self.to_decision(
            response=response,
            input_context=self.snapshot_context(messages, tools, "chat"),
        )

    async def generate_final_decision(
        self,
        *,
        messages: list[dict[str, Any]],
        channel_overrides: Optional[dict[str, Any]] = None,
    ) -> BrainDecision:
        response = await self.generate_final(
            messages=messages,
            channel_overrides=channel_overrides,
        )
        return self.to_decision(
            response=response,
            input_context=self.snapshot_context(messages, None, "chat"),
        )

    def to_decision(
        self,
        *,
        response: Any,
        input_context: dict[str, Any],
    ) -> BrainDecision:
        raw_tool_calls = list(getattr(response, "tool_calls", None) or [])
        stop_reason = getattr(response, "stop_reason", "")
        stop_reason_text = stop_reason.value if hasattr(stop_reason, "value") else str(stop_reason)
        return BrainDecision(
            content=getattr(response, "content", None) or "",
            action_requests=[ActionRequest.from_tool_call(tc) for tc in raw_tool_calls],
            stop_reason=stop_reason_text,
            input_context=input_context,
            raw_response=response,
            raw_tool_calls=raw_tool_calls,
        )

    async def generate_final(
        self,
        *,
        messages: list[dict[str, Any]],
        channel_overrides: Optional[dict[str, Any]] = None,
    ) -> Any:
        if self._model is None:
            raise RuntimeError("BrainEngine requires resource_pool.model")

        overrides = channel_overrides or {}
        return await self._model.generate(
            model_selector="chat",
            messages=messages,
            stream=overrides.get("stream", False),
            max_tokens=overrides.get("max_tokens"),
            latency_budget=overrides.get("timeout"),
        )

    def snapshot_context(
        self,
        context: list[dict[str, Any]],
        tools: Optional[list[dict[str, Any]]] = None,
        model_selector: str = "chat",
    ) -> dict[str, Any]:
        return {
            "model_selector": model_selector,
            "messages": [
                {
                    "role": m.get("role"),
                    "content": m.get("content"),
                    **({"tool_call_id": m.get("tool_call_id")} if m.get("tool_call_id") else {}),
                    **({"name": m.get("name")} if m.get("name") else {}),
                }
                for m in context
            ],
            "tools": [
                {"type": t.get("type"), "function": t.get("function")}
                for t in (tools or [])
            ],
            "estimated_tokens": estimate_messages_tokens(context),
        }
