"""ActionRuntime —— 工具选择、执行、审计和结果后处理的统一入口。

Durable Activities use an explicit resource key and execution identity for every
operation; the runtime has no surface-specific or session-only fallback.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, cast

from actions.contracts import ActionRequest, ActionResult
from common.agent_policy import AgentPolicy
from common.logging import log_event
from common.token_counter import estimate_messages_tokens
from sandbox.tools.routing.models import ToolRetrievalError, ToolSelectionResult, retrieval_failure

logger = logging.getLogger("HpAgent.ActionRuntime")


class ActionRuntime:
    """行动运行时 facade。

    这一层保留 Sandbox 的核心能力，同时把工具 RAG 查询拼接、
    审计事件写入和长结果摘要从执行 Host 中移出。
    """

    def __init__(
        self,
        *,
        sandbox_manager: Any = None,
        resource_pool: Any = None,
        prompts: Any = None,
        tool_rag_top_k: int = 12,
        tool_result_summary_enabled: bool = AgentPolicy.tool_result_summary_enabled,
        tool_result_summary_threshold: int = AgentPolicy.tool_result_summary_threshold,
        tool_result_summary_max_chars: int = AgentPolicy.tool_result_summary_max_chars,
    ):
        self._sandbox = sandbox_manager
        self._model = resource_pool
        self._prompts = prompts
        self._tool_rag_top_k = tool_rag_top_k
        self._tool_result_summary_enabled = tool_result_summary_enabled
        self._tool_result_summary_threshold = tool_result_summary_threshold
        self._tool_result_summary_max_chars = tool_result_summary_max_chars

    @staticmethod
    def _validate_execution_identity(resource_key: str, execution_id: str) -> None:
        if not resource_key or not execution_id:
            raise ValueError("resource_key and execution_id are required")

    def reset_execution(self, resource_key: str, execution_id: str) -> None:
        """Clear only the mutable state owned by this execution."""
        self._validate_execution_identity(resource_key, execution_id)
        if self._sandbox is None:
            return
        try:
            sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
            sandbox.reset_hints()
        except Exception:
            pass

    async def select_tools(
        self,
        *,
        user_content: str = "",
        resource_key: str,
        execution_id: str,
        group_context_text: str = "",
    ) -> ToolSelectionResult:
        """Select against fresh hints, context and permissions on every call."""
        self._validate_execution_identity(resource_key, execution_id)
        started = time.monotonic()
        try:
            if self._sandbox is None:
                raise ToolRetrievalError("selection", "sandbox_unavailable")
            sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
            result = await sandbox.select_tools(
                self._build_rag_query(user_content, group_context_text), self._tool_rag_top_k
            )
        except Exception as exc:
            error = retrieval_failure("selection", exc)
            result = ToolSelectionResult((), {"reasons": [{"stage": error.stage, "code": error.code}]}, "failed", error)
        # Audit fields are selected explicitly: query text and resource names do
        # not belong in process logs. The complete audit stays on the result.
        log_event(logger, logging.ERROR if result.status == "failed" else logging.INFO,
                  "tool_selection", "tools", resource_key=resource_key, execution_id=execution_id,
                  status=result.status, rag_enabled=result.audit.get("rag_enabled"),
                  ordinary_candidate_count=len(result.tool_schemas),
                  tools=[tool.get("function", tool).get("name") for tool in result.tool_schemas],
                  reasons=result.audit.get("reasons", []),
                  elapsed_ms=round((time.monotonic() - started) * 1000))
        return result

    async def _execute(
        self,
        *,
        tool_name: str,
        arguments: Dict[str, Any],
        resource_key: str,
        execution_id: str,
        user_query: str = "",
        agent_policy: AgentPolicy | None = None,
    ) -> Dict[str, Any]:
        """执行工具，并按配置对长输出做语义摘要。"""
        self._validate_execution_identity(resource_key, execution_id)
        if self._sandbox is None:
            return {
                "success": False,
                "output": None,
                "error": "SandboxManager not configured",
                "metadata": {},
            }

        try:
            sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
            result, _audit = await sandbox.execute(tool_name, arguments)
            result_dict = cast(Dict[str, Any], result.to_dict())
            result_dict.setdefault("success", result_dict.get("error") is None)
            result_dict.setdefault("output", None)
            result_dict.setdefault("error", None)
            result_dict.setdefault("metadata", {})

            if (agent_policy.tool_result_summary_enabled if agent_policy else self._tool_result_summary_enabled):
                result_dict = await self._summarize_if_needed(
                    result_dict, tool_name, user_query, resource_key, agent_policy=agent_policy,
                )

            return result_dict
        except Exception as e:
            return {
                "success": False,
                "output": None,
                "error": str(e),
                "metadata": {},
            }


    async def execute_request(
        self,
        request: ActionRequest,
        *,
        resource_key: str,
        execution_id: str,
        user_query: str = "",
        idempotency_key: str = "",
        agent_policy: AgentPolicy | None = None,
    ) -> ActionResult:
        self._validate_execution_identity(resource_key, execution_id)
        executed_request = request
        if idempotency_key and self._sandbox is not None:
            try:
                sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
                key_argument = sandbox.get_tool_metadata(request.name).get(
                    "idempotency_key_argument"
                )
            except Exception:
                key_argument = None
            if isinstance(key_argument, str) and key_argument:
                arguments = dict(request.arguments)
                # The runtime owns durable operation identity. Never allow a
                # model-supplied value to select another idempotency record.
                arguments[key_argument] = idempotency_key
                executed_request = ActionRequest(
                    request.id,
                    request.name,
                    arguments,
                )
        result = await self._execute(
            tool_name=executed_request.name,
            arguments=executed_request.arguments,
            resource_key=resource_key,
            execution_id=execution_id,
            user_query=user_query,
            agent_policy=agent_policy,
        )
        metadata = dict(result.get("metadata") or {})
        metadata.setdefault(
            "invocation_key",
            f"tool-invocation:{execution_id}:{request.id}",
        )
        if idempotency_key:
            # Providers that expose an idempotency facility can consume
            # this stable operation identifier; it is also retained in the
            # result for reconciliation/audit adapters.
            metadata.setdefault("idempotency_key", idempotency_key)
        result["metadata"] = metadata
        return ActionResult.from_runtime_result(request, result)

    def clear_execution(self, resource_key: str, execution_id: str) -> None:
        """Validate the execution identity; selection has no retained cache state."""
        self._validate_execution_identity(resource_key, execution_id)

    def side_effect_class(self, resource_key: str, tool_name: str) -> str:
        """Read trusted registry metadata; unknown tools fail closed upstream."""
        if self._sandbox is None:
            return "unknown"
        try:
            sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
            value = sandbox.get_tool_metadata(tool_name).get(
                "side_effect_class", "unknown"
            )
            return str(value)
        except Exception:
            return "unknown"

    def tool_category(self, resource_key: str, tool_name: str) -> str | None:
        if self._sandbox is None:
            return None
        sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
        return sandbox.get_tool_category(tool_name)

    def budget_reservation(self, resource_key: str, tool_name: str) -> Dict[str, int]:
        """Return trusted per-tool reservation bounds from registry metadata."""
        if self._sandbox is None:
            return {"tool_calls": 1}
        try:
            sandbox = self._sandbox.get_sandbox_for_execution(resource_key)
            configured = sandbox.get_tool_metadata(tool_name).get(
                "budget_reservation"
            )
            if not isinstance(configured, dict):
                return {"tool_calls": 1}
            values = {
                str(dimension): int(amount)
                for dimension, amount in configured.items()
                if isinstance(dimension, str)
                and isinstance(amount, int)
                and not isinstance(amount, bool)
                and amount >= 0
            }
            values["tool_calls"] = max(1, values.get("tool_calls", 0))
            return values
        except Exception:
            return {"tool_calls": 1}

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
        *, agent_policy: AgentPolicy | None = None,
    ) -> Dict[str, Any]:
        threshold = agent_policy.tool_result_summary_threshold if agent_policy else self._tool_result_summary_threshold
        max_chars = agent_policy.tool_result_summary_max_chars if agent_policy else self._tool_result_summary_max_chars
        output = result_dict.get("output")
        if not output or not isinstance(output, str):
            return result_dict
        if len(output) <= threshold:
            return result_dict
        if self._model is None or self._prompts is None:
            result_dict["output"] = output[:max_chars]
            return result_dict

        try:
            hint = self._prompts.get_tool_summary_hint(tool_name)
            template = self._prompts.get_tool_summary_template()
            system_prompt = template.format(
                tool_name=tool_name,
                max_chars=max_chars,
            )
            user_prompt = (
                f"用户问题：{user_query}\n"
                f"关注点：{hint}\n\n"
                f"工具输出（{len(output)}字符）：\n{output[:threshold * 2]}"
            ) if user_query else (
                f"关注点：{hint}\n\n"
                f"工具输出（{len(output)}字符）：\n{output[:threshold * 2]}"
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
            else:
                result_dict["output"] = output[:max_chars]
        except Exception as e:
            logger.warning(
                "Tool summary failed for %s: %s, falling back to truncation",
                tool_name, e,
            )
            result_dict["output"] = output[:max_chars]

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
