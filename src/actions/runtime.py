"""ActionRuntime —— 工具选择、执行、审计和结果后处理的统一入口。

HarnessRunner 不再直接面向 SandboxManager/Sandbox。
它只向 ActionRuntime 请求：
  - reset_turn(session_id)
  - select_tools(...)
  - execute(...)
  - clear_session(session_id)
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Dict, List

from common.token_counter import estimate_messages_tokens
from common.types import Event, EventType
from agent.protocol import ActionRequest, ActionResult

logger = logging.getLogger("HpAgent.ActionRuntime")


class ActionRuntime:
    """行动运行时 facade。

    这一层保留 Sandbox 的核心能力，同时把工具 RAG 查询拼接、缓存、
    审计事件写入和长结果摘要从 HarnessRunner 中移出。
    """

    def __init__(
        self,
        *,
        sandbox_manager: Any = None,
        session_store: Any = None,
        resource_pool: Any = None,
        prompts: Any = None,
        tool_rag_top_k: int = 8,
        tool_result_summary_enabled: bool = True,
        tool_result_summary_threshold: int = 4000,
        tool_result_summary_max_chars: int = 1000,
    ):
        self._sandbox = sandbox_manager
        self._session = session_store
        self._model = resource_pool
        self._prompts = prompts
        self._tool_rag_top_k = tool_rag_top_k
        self._tool_result_summary_enabled = tool_result_summary_enabled
        self._tool_result_summary_threshold = tool_result_summary_threshold
        self._tool_result_summary_max_chars = tool_result_summary_max_chars
        self._tools_cache: Dict[str, tuple] = {}

    def reset_turn(self, session_id: str) -> None:
        """清理上一轮残留的 tool hints。"""
        if self._sandbox is None:
            return
        try:
            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            sandbox.reset_hints()
        except Exception:
            pass

    async def select_tools(
        self,
        *,
        user_content: str = "",
        session_id: str = "",
        group_context_text: str = "",
    ) -> List[Dict[str, Any]]:
        """完成工具选择管线，并写入工具检索审计事件。"""
        rag_query = self._build_rag_query(user_content, group_context_text)
        if self._sandbox is None:
            return []

        try:
            if session_id:
                query_hash = hashlib.md5(user_content.encode()).hexdigest()
                if session_id in self._tools_cache:
                    last_hash, cached = self._tools_cache[session_id]
                    if query_hash == last_hash:
                        logger.info("select_tools: cache hit session=%s", session_id)
                        return cached

            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            tools, audit = await sandbox.select_tools(rag_query, self._tool_rag_top_k)

            if session_id:
                self._tools_cache[session_id] = (query_hash, tools)

            logger.info(
                "select_tools: query=%s top_k=%d -> %d tools",
                audit.get("queries", [rag_query])[0][:80],
                self._tool_rag_top_k,
                len(tools),
            )

            if session_id and self._session is not None:
                await self._session.append_events(session_id, Event(
                    session_id=session_id,
                    event_type=EventType.TOOL_RETRIEVAL,
                    content=audit,
                ))

            return tools
        except Exception as e:
            logger.warning("Tool retrieval failed for sid=%s: %s", session_id, e)
            return []

    async def execute(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        session_id: str = "",
        user_query: str = "",
    ) -> Dict[str, Any]:
        """执行工具，并按配置对长输出做语义摘要。"""
        if self._sandbox is None:
            return {"output": None, "error": "SandboxManager not configured"}

        try:
            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            result, _audit = await sandbox.execute(tool_name, arguments)
            result_dict = result.to_dict()

            if self._tool_result_summary_enabled:
                result_dict = await self._summarize_if_needed(
                    result_dict, tool_name, user_query, session_id,
                )

            return result_dict
        except Exception as e:
            return {"output": None, "error": str(e)}


    async def execute_request(
        self,
        request: ActionRequest,
        *,
        session_id: str = "",
        user_query: str = "",
    ) -> ActionResult:
        result = await self.execute(
            tool_name=request.name,
            arguments=request.arguments,
            session_id=session_id,
            user_query=user_query,
        )
        return ActionResult.from_runtime_result(request, result)

    def clear_session(self, session_id: str) -> None:
        """会话结束时清理行动运行时的会话级缓存。"""
        self._tools_cache.pop(session_id, None)

    def _build_rag_query(self, user_content: str, group_context_text: str) -> str:
        rag_query = user_content
        if group_context_text:
            recent_msgs = [
                m.strip()
                for m in group_context_text.strip().split("\n")[-3:]
                if m.strip()
            ]
            if recent_msgs:
                rag_query = f"{user_content}\n--- 群聊 ---\n" + "\n".join(recent_msgs)
        return rag_query

    async def _summarize_if_needed(
        self,
        result_dict: Dict[str, Any],
        tool_name: str,
        user_query: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        output = result_dict.get("output")
        if not output or not isinstance(output, str):
            return result_dict
        if len(output) <= self._tool_result_summary_threshold:
            return result_dict
        if self._model is None or self._prompts is None:
            result_dict["output"] = output[:self._tool_result_summary_max_chars]
            return result_dict

        try:
            hint = self._prompts.get_tool_summary_hint(tool_name)
            template = self._prompts.get_tool_summary_template()
            system_prompt = template.format(
                tool_name=tool_name,
                max_chars=self._tool_result_summary_max_chars,
            )
            user_prompt = (
                f"用户问题：{user_query}\n"
                f"关注点：{hint}\n\n"
                f"工具输出（{len(output)}字符）：\n{output[:self._tool_result_summary_threshold * 2]}"
            ) if user_query else (
                f"关注点：{hint}\n\n"
                f"工具输出（{len(output)}字符）：\n{output[:self._tool_result_summary_threshold * 2]}"
            )

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            response = await self._model.generate(
                model_selector="fast",
                messages=messages,
                stream=False,
            )
            summary = (response.content or "").strip()
            if summary:
                logger.debug(
                    "Tool result summarized: %s %d->%d chars",
                    tool_name, len(output), len(summary),
                )
                result_dict["summary"] = summary
                result_dict["metadata"] = result_dict.get("metadata") or {}
                result_dict["metadata"]["summarized"] = True
                if session_id and self._session is not None:
                    await self._session.append_events(session_id, Event(
                        session_id=session_id,
                        event_type=EventType.TOOL_SUMMARY,
                        content={
                            "tool_name": tool_name,
                            "original_chars": len(output),
                            "summary_chars": len(summary),
                            "summary": summary,
                            "input_context": self._snapshot_context(
                                messages, model_selector="fast",
                            ),
                        },
                    ))
            else:
                result_dict["output"] = output[:self._tool_result_summary_max_chars]
        except Exception as e:
            logger.warning(
                "Tool summary failed for %s: %s, falling back to truncation",
                tool_name, e,
            )
            result_dict["output"] = output[:self._tool_result_summary_max_chars]

        return result_dict

    def _snapshot_context(
        self,
        context: List[Dict[str, Any]],
        tools: List[Dict[str, Any]] | None = None,
        model_selector: str = "",
    ) -> Dict[str, Any]:
        messages: list[dict] = []
        for msg in context:
            m = dict(msg)
            content = m.get("content", "")
            if isinstance(content, str) and len(content) > 8000:
                m["content"] = content[:8000] + "... [TRUNCATED]"
            messages.append(m)
        return {
            "messages": messages,
            "input_tokens": estimate_messages_tokens(messages),
            "tool_count": len(tools) if tools else 0,
            "model_selector": model_selector,
        }
