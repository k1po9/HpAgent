"""
HarnessRunner —— 无状态协调器。

Harness 是 Temporal Activities 唯一的交互对象。
持有 SessionStore / ContextBuilder / ResourcePool / SandboxManager / ChannelRouter，
在 process_turn() 中协调完整的 agentic loop。

Temporal Workflow 只做编排（何时处理消息），Harness 做执行（如何调用模型、工具、记忆）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from common.types import (
    Event,
    EventType,
    ChannelType,
    UnifiedMessage,
    ToolResult as CommonToolResult,
)
from session.store import SessionStore
from sandbox.channels.router import ChannelRouter
from agent.runner import MultiAgentExecutor
from harness.context_builder import HarnessContextBuilder
from resources.resource_pool import ResourcePool
from sandbox.sandbox_manager import SandboxManager

logger = logging.getLogger("HpAgent.HarnessRunner")


class HarnessRunner:
    """无状态协调器 —— 聚合记忆 / 上下文 / 模型 / 工具 / 渠道。

    Usage::

        harness = HarnessRunner(session_store, ctx_builder, pool, sandbox_mgr, router)
        result = await harness.process_turn(account_id, session_id, user_message)
    """

    def __init__(
        self,
        session_store: SessionStore,
        context_builder: Optional[HarnessContextBuilder] = None,
        resource_pool: Optional[ResourcePool] = None,
        sandbox_manager: Optional[SandboxManager] = None,
        channel_router: Optional[ChannelRouter] = None,
        max_tool_turns: int = 20,
        agent_mode: str = "single",
        multi_agent_executor: Optional[MultiAgentExecutor] = None,
    ):
        self._session = session_store
        self._ctx = context_builder
        self._model = resource_pool
        self._sandbox = sandbox_manager
        self._channel = channel_router
        self._max_tool_turns = max_tool_turns
        self._agent_mode = agent_mode
        self._multi_agent_executor = multi_agent_executor

    # ═══════════════════════════════════════════════════════════════════════════
    # 主入口: process_turn
    # ═══════════════════════════════════════════════════════════════════════════

    async def process_turn(
        self,
        user_message: Dict[str, Any],
    ) -> Dict[str, Any]:
        """处理一个完整的对话轮次（agentic loop）。

        Args:
            user_message: 用户消息 dict，含 content / sender_id / channel_type 等。

        Returns:
            {"content": str, "turns": int, "session_id": str, "account_id": str}
        """
        account_id = user_message["account_id"]
        session_id = user_message["session_id"]
        channel_type_str = user_message["channel_type"]
        channel_type = self._resolve_channel(channel_type_str)
        user_content = user_message["content"]
        sender_id = user_message["sender_id"]
        metadata = user_message["metadata"]

        # 确保会话已创建 + 工作区就绪
        await self._ensure_session(session_id, account_id, channel_type_str)

        # 追加用户消息事件
        user_event = Event(
            session_id=session_id,
            event_type=EventType.USER_MESSAGE,
            content={
                "content": user_content,
                "sender_id": sender_id,
                "channel_type": channel_type_str,
                "account_id": account_id,
            },
            metadata=metadata
        )
        await self._session.append_events(session_id, user_event)

        # 加载历史事件
        events = await self._session.get_events(session_id, limit=100)

        # 收集本轮事件（用于 retain）
        turn_events: List[Dict[str, Any]] = [
            {"role": "user", "content": user_content}
        ]

        final_content = ""
        turns_taken = 0

        # ── Multi-Agent Path ────────────────────────────────────────────
        if self._agent_mode == "multi" and self._multi_agent_executor is not None:
            # recall memory (with channel-aware isolation)
            _mem_items, memories_text = await self._session.recall_memories(
                query=user_content,
                account_id=account_id,
                session_id=session_id,
                top_n=5,
                tags_match="any_strict",
                query_timestamp=metadata.get("iso_timestamp", ""),
                group_id=str(metadata.get("group_id", "")),
                scope=metadata.get("detail_type", ""),
                channel_type=channel_type_str,
            )

            final_content, turns_taken = await self._multi_agent_executor.execute(
                goal=user_content,
                history_events=events,
                memories_text=memories_text,
            )

            # Record model event for the final synthesized response
            model_event = Event(
                session_id=session_id,
                event_type=EventType.MODEL_MESSAGE,
                content={
                    "text": final_content,
                    "tool_calls": [],
                    "stop_reason": "end_turn",
                },
            )
            events.append(model_event)
            await self._session.append_events(session_id, model_event)

            turn_events.append({
                "role": "assistant",
                "content": final_content,
            })

        # ── Single-Agent Path (existing ReAct loop, unchanged) ───────────
        else:
            while turns_taken < self._max_tool_turns:
                turns_taken += 1

                # 召回长期记忆（渠道感知的标签隔离）
                memories_items, memories_text = await self._session.recall_memories(
                    query=user_content,
                    account_id=account_id,
                    session_id=session_id,
                    top_n=5,
                    tags_match="any_strict",
                    query_timestamp=metadata.get("iso_timestamp", ""),
                    group_id=str(metadata.get("group_id", "")),
                    scope=metadata.get("detail_type", ""),
                    channel_type=channel_type_str,
                )

                # 构建上下文
                context = self._build_context(events, channel_type, memories_text)

                # 获取工具列表（支持 RAG 动态注入）
                tools = await self._get_tools(user_content, session_id)

                # 调用模型
                for turn in context:
                    logger.info("Context turn: %s", turn)
                response = await self._model.generate(
                    model_selector="chat",
                    messages=context,
                    tools=tools if tools else None,
                    stream=False,
                )

                final_content = response.content or ""

                # 追加模型回复事件
                model_event = Event(
                    session_id=session_id,
                    event_type=EventType.MODEL_MESSAGE,
                    content={
                        "text": response.content or "",
                        "tool_calls": [
                            tc.to_dict() for tc in (response.tool_calls or [])
                        ],
                        "stop_reason": (
                            response.stop_reason.value
                            if hasattr(response.stop_reason, "value")
                            else str(response.stop_reason)
                        ),
                    },
                )
                events.append(model_event)
                await self._session.append_events(session_id, model_event)

                # 处理工具调用
                if response.tool_calls:
                    assistant_turn = {
                        "role": "assistant",
                        "content": response.content or "",
                        "tool_calls": [
                            {"name": tc.name, "arguments": tc.arguments}
                            for tc in response.tool_calls
                        ],
                    }
                    turn_events.append(assistant_turn)

                    for tc in response.tool_calls:
                        result = await self._execute_tool(tc.name, tc.arguments, session_id)
                        tool_event = Event(
                            session_id=session_id,
                            event_type=EventType.TOOL_RESULT,
                            content={
                                "tool_call_id": tc.id,
                                "tool_name": tc.name,
                                "result": result.get("output"),
                                "error": result.get("error"),
                            },
                        )
                        events.append(tool_event)
                        await self._session.append_events(session_id, tool_event)

                    # 工具结果注入后继续循环，让模型看到结果
                    continue
                else:
                    turn_events.append({
                        "role": "assistant",
                        "content": final_content,
                    })
                    break  # 无工具调用，本轮结束

        # 发送响应
        await self._send_response(final_content, user_message)

        # 提取长期记忆（异步提交，不阻塞 Temporal Activity 完成）
        await self._session.retain_memories(
            turn_events, account_id, session_id,
            channel_type=channel_type_str,
            group_id=str(metadata.get("group_id", "")),
            sender_name=metadata.get("sender_name", ""),
            iso_timestamp=metadata.get("iso_timestamp", ""),
            scope=metadata.get("detail_type", ""),
        )

        # 每 10 轮输出一次可观测性指标快照（结构化日志，供监控采集）
        if turns_taken > 0 and turns_taken % 10 == 0 and self._session._hindsight:
            self._session._hindsight.log_metrics()

        return {
            "content": final_content,
            "turns": turns_taken,
            "session_id": session_id,
            "account_id": account_id,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════════════════

    async def _ensure_session(
        self, session_id: str, account_id: str, channel_type: str
    ) -> None:
        """确保会话已在 SessionStore 中创建（幂等）。"""
        existing = await self._session.get_session(session_id)
        if existing is None:
            await self._session.create_session(
                session_id=session_id,
                account_id=account_id,
                channel_type=channel_type,
            )

    def _resolve_channel(self, raw: str) -> Optional[ChannelType]:
        try:
            return ChannelType(raw)
        except ValueError:
            return None

    def _build_context(
        self,
        events: List[Event],
        channel_type: Optional[ChannelType],
        memories_text: str,
    ) -> List[Dict[str, Any]]:
        """将事件历史 + 记忆组装为 LLM messages。"""
        if self._ctx is None:
            return [{"role": "user", "content": ""}]
        return self._ctx.build(
            events=events,
            channel_type=channel_type,
            recalled_memories=memories_text,
            max_turns=20,
        )

    async def _get_tools(self, user_content: str = "", session_id: str = "") -> List[Dict[str, Any]]:
        if self._sandbox is None:
            return []
        try:
            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            if sandbox._registry._retriever is not None and user_content:
                return await sandbox._registry.retrieve_for_llm(
                    query=user_content, top_k=8
                )
            return sandbox.list_tools()
        except Exception:
            return []

    async def _execute_tool(
        self, tool_name: str, arguments: Dict[str, Any], session_id: str = ""
    ) -> Dict[str, Any]:
        if self._sandbox is None:
            return {"output": None, "error": "SandboxManager not configured"}
        try:
            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            result = await sandbox.execute(tool_name, arguments)
            return result.to_dict()
        except Exception as e:
            return {"output": None, "error": str(e)}

    async def reflect(self, account_id: str) -> Dict[str, Any]:
        """触发长期记忆深度推理。"""
        count = await self._session.reflect(account_id)
        return {"insights": count}

    async def get_metrics(self) -> Dict[str, Any]:
        """返回 Hindsight 客户端可观测性指标快照。"""
        if self._session._hindsight:
            return self._session._hindsight.get_metrics()
        return {}

    async def _send_response(
        self,
        content: str,
        user_message: Dict[str, Any],
    ) -> bool:
        """通过 ChannelRouter 发送回复。"""
        if self._channel is None:
            return False

        ch_type_str = user_message.get("channel_type", "console")
        ch_type = self._resolve_channel(ch_type_str)

        msg = UnifiedMessage(
            session_id=user_message["session_id"],
            account_id=user_message["account_id"],
            sender_id=user_message["sender_id"],
            channel_type=ch_type,
            content=content,
            metadata=user_message.get("metadata", {}),
        )
        try:
            return await self._channel.send(msg)
        except Exception as e:
            logger.warning("Channel send failed: %s", e)
            return False
