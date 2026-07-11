"""
TurnOrchestrator —— 单轮对话流程导演。

TurnOrchestrator 是 Temporal Activities 注入对象的正式名称。
它持有 TurnMemoryService / ContextBuilder / BrainEngine / ActionRuntime / ReplyService，
在 process_turn() 中协调完整的 agentic loop。

HarnessRunner 作为兼容名称保留，避免旧 import 和测试立即失效。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from common.types import (
    Event,
    ChannelType,
)
from session.store import SessionStore
from channels.router import ChannelRouter
from agent.runner import MultiAgentExecutor
from actions.runtime import ActionRuntime
from brain.engine import BrainEngine
from application.memory import TurnMemoryService
from application.reply import ReplyService
from harness.context_builder import HarnessContextBuilder
from resources.resource_pool import ResourcePool
from sandbox.sandbox_manager import SandboxManager

logger = logging.getLogger("HpAgent.TurnOrchestrator")


class TurnOrchestrator:
    """一轮对话流程导演 —— 编排记忆 / 大脑 / 行动 / 回复。

    Usage::

        turn = TurnOrchestrator(session_store, ctx_builder, pool, sandbox_mgr, router)
        result = await turn.process_turn(user_message)
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
        channel_overrides: Optional[Dict[str, Any]] = None,
        git_repo_manager: Any = None,
        workspace_db: Any = None,
        file_store: Any = None,
        prompts: Any = None,
        context_budget: int = 0,
        generation_headroom: int = 4000,
        summary_budget: int = 2000,
        memories_budget: int = 2000,
        compress_interval: int = 8,
        checkpoint_interval: int = 10,
        tool_result_summary_enabled: bool = True,
        tool_result_summary_threshold: int = 4000,
        tool_result_summary_max_chars: int = 1000,
        tool_rag_top_k: int = 8,
        group_context: Any = None,
        reply_service: Optional[ReplyService] = None,
        action_runtime: Optional[ActionRuntime] = None,
        brain_engine: Optional[BrainEngine] = None,
        memory_service: Optional[TurnMemoryService] = None,
    ):
        self._session = session_store  # Backward-compatible access; prefer self._memory.
        self._memory = memory_service or TurnMemoryService(session_store=session_store)
        self._ctx = context_builder
        self._model = resource_pool
        self._group_context = group_context  # GroupContextStore | None
        self._reply = reply_service or ReplyService(
            channel_router=channel_router,
            group_context=group_context,
            prompts=prompts,
        )
        self._brain = brain_engine or BrainEngine(
            resource_pool=resource_pool,
            prompts=prompts,
        )
        self._actions = action_runtime or ActionRuntime(
            sandbox_manager=sandbox_manager,
            session_store=session_store,
            resource_pool=resource_pool,
            prompts=prompts,
            tool_rag_top_k=tool_rag_top_k,
            tool_result_summary_enabled=tool_result_summary_enabled,
            tool_result_summary_threshold=tool_result_summary_threshold,
            tool_result_summary_max_chars=tool_result_summary_max_chars,
        )
        self._max_tool_turns = max_tool_turns
        self._agent_mode = agent_mode
        self._multi_agent_executor = multi_agent_executor
        self._channel_overrides = channel_overrides
        self._git_repo_manager = git_repo_manager
        self._workspace_db = workspace_db
        self._file_store = file_store
        self._prompts = prompts
        self._context_budget = context_budget
        self._generation_headroom = generation_headroom
        self._summary_budget = summary_budget
        self._memories_budget = memories_budget
        self._compress_interval = compress_interval
        self._checkpoint_interval = checkpoint_interval
        self._tool_result_summary_enabled = tool_result_summary_enabled
        self._tool_result_summary_threshold = tool_result_summary_threshold
        self._tool_result_summary_max_chars = tool_result_summary_max_chars
        self._tool_rag_top_k = tool_rag_top_k
        # HyDE 改写上下文（用于审计展示）
        self._last_hyde_context: Optional[List[Dict[str, str]]] = None

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

        # 清理上一轮的 HyDE 上下文，防止跨 turn 泄漏
        self._last_hyde_context = None

        # 确保会话已创建 + 工作区就绪
        await self._memory.ensure_session(session_id, account_id, channel_type_str)

        # ── 群聊上下文：订阅 + 获取上下文文本 ──
        group_context_text = ""
        group_id = str(metadata.get("group_id", ""))
        if group_id and self._group_context:
            try:
                # 订阅（幂等）
                await self._group_context.subscribe(group_id, session_id)
                # 获取当前窗口内容注入 prompt
                group_context_text = await self._group_context.get_window(group_id)
            except Exception:
                logger.warning("Failed to fetch group context for group %s", group_id)

        # 清理上一轮残留的 tool hints，防止跨轮次泄漏到 RAG 检索
        self._actions.reset_turn(session_id)

        # 追加用户消息事件
        await self._memory.record_user_message(
            session_id=session_id,
            content=user_content,
            sender_id=sender_id,
            channel_type=channel_type_str,
            account_id=account_id,
            metadata=metadata,
        )

        # 加载历史事件
        events = await self._memory.load_recent_events(session_id, limit=100)

        # 收集本轮事件（用于 retain）
        turn_events: List[Dict[str, Any]] = [
            {"role": "user", "content": user_content}
        ]

        final_content = ""
        turns_taken = 0

        # ── Multi-Agent Path ────────────────────────────────────────────
        if self._agent_mode == "multi" and self._multi_agent_executor is not None:
            # HyDE 改写 query 以提升记忆召回命中率
            recall_query, self._last_hyde_context = await self._brain.rewrite_recall_query(
                user_content=user_content,
                group_context_text=group_context_text,
                sender_name=metadata.get("sender_name", ""),
            )
            # recall memory (with channel-aware isolation)
            _mem_items, memories_text = await self._memory.recall_memories(
                query=recall_query,
                account_id=account_id,
                session_id=session_id,
                channel_type=channel_type_str,
                metadata=metadata,
                original_query=user_content,
                rewritten_query=recall_query,
                hyde_input_context=getattr(self, '_last_hyde_context', None),
            )

            final_content, turns_taken = await self._multi_agent_executor.execute(
                goal=user_content,
                history_events=events,
                memories_text=memories_text,
            )

            # Record model event for the final synthesized response
            model_event = await self._memory.record_model_message(
                session_id=session_id,
                text=final_content,
                tool_calls=[],
                stop_reason="end_turn",
            )
            events.append(model_event)

            turn_events.append({
                "role": "assistant",
                "content": final_content,
            })

        # ── Single-Agent Path (existing ReAct loop, unchanged) ───────────
        else:
            # HyDE 改写 query 以提升记忆召回命中率
            recall_query, self._last_hyde_context = await self._brain.rewrite_recall_query(
                user_content=user_content,
                group_context_text=group_context_text,
                sender_name=metadata.get("sender_name", ""),
            )
            # 召回长期记忆（仅在首轮执行一次，渠道感知的标签隔离）
            _mem_items, memories_text = await self._memory.recall_memories(
                query=recall_query,
                account_id=account_id,
                session_id=session_id,
                channel_type=channel_type_str,
                metadata=metadata,
                original_query=user_content,
                rewritten_query=recall_query,
                hyde_input_context=getattr(self, '_last_hyde_context', None),
            )

            while turns_taken < self._max_tool_turns:
                turns_taken += 1

                # 构建上下文（注入剩余轮次感知）
                remaining = self._max_tool_turns - turns_taken + 1
                context = self._build_context(
                    events, channel_type, memories_text,
                    remaining_turns=remaining,
                    max_tool_turns=self._max_tool_turns,
                    group_context_text=group_context_text,
                )

                # 获取工具列表（支持 RAG 动态注入，群聊时带最近消息提升相关性）
                tools = await self._actions.select_tools(
                    user_content=user_content,
                    session_id=session_id,
                    group_context_text=group_context_text,
                )

                # 调用模型
                # for turn in context:
                #     logger.info("Context turn: %s", turn)
                ch_overrides = self._channel_overrides.get(channel_type_str, {}) if self._channel_overrides else {}
                decision = await self._brain.generate_chat_decision(
                    messages=context,
                    tools=tools if tools else None,
                    channel_overrides=ch_overrides,
                )

                final_content = decision.content

                # 追加模型回复事件
                model_event = await self._memory.record_model_message(
                    session_id=session_id,
                    text=decision.content,
                    tool_calls=decision.model_event_tool_calls(),
                    stop_reason=decision.stop_reason,
                    input_context=decision.input_context,
                )
                events.append(model_event)

                # 处理工具调用
                if decision.has_actions:
                    assistant_turn = {
                        "role": "assistant",
                        "content": decision.content,
                        "tool_calls": [
                            request.to_assistant_tool_call()
                            for request in decision.action_requests
                        ],
                    }
                    turn_events.append(assistant_turn)

                    # 群聊低密度时发送工具进度提示
                    await self._reply.send_progress(decision.raw_tool_calls or [], user_message)

                    for request in decision.action_requests:
                        action_result = await self._actions.execute_request(
                            request,
                            session_id=session_id,
                            user_query=user_content,
                        )
                        tool_event = await self._memory.record_tool_result(
                            session_id=session_id,
                            tool_call_id=request.id,
                            tool_name=request.name,
                            result=action_result.display_result,
                            error=action_result.error,
                            original_output=action_result.output,
                            metadata=action_result.metadata,
                        )
                        events.append(tool_event)

                    # 工具结果注入后继续循环，让模型看到结果
                    continue
                else:
                    turn_events.append({
                        "role": "assistant",
                        "content": final_content,
                    })
                    break  # 无工具调用，本轮结束

        # ── 兜底：所有工具轮次耗尽仍未得到文本回复时，补一次最终生成 ──
        if not final_content.strip() and turns_taken >= self._max_tool_turns:
            context = self._build_context(
                events, channel_type, memories_text,
                remaining_turns=0,
                max_tool_turns=self._max_tool_turns,
                group_context_text=group_context_text,
            )
            try:
                ch_overrides = self._channel_overrides.get(channel_type_str, {}) if self._channel_overrides else {}
                decision = await self._brain.generate_final_decision(
                    messages=context,
                    channel_overrides=ch_overrides,
                )
                final_content = decision.content
                model_event = await self._memory.record_model_message(
                    session_id=session_id,
                    text=final_content,
                    tool_calls=[],
                    stop_reason="forced_final",
                    input_context=decision.input_context,
                )
                events.append(model_event)
                turn_events.append({"role": "assistant", "content": final_content})
            except Exception:
                logger.exception("Fallback final generation failed for session %s", session_id)
        # ── 极端兜底：模型仍返回空，给一句降级回复 ──
        if not final_content.strip():
            final_content = "抱歉，我暂时无法处理这个消息，请稍后再试。"

        # ── 最终安全网：检查并清理可能泄露的 XML 工具调用 ──
        if final_content and "<tool_call>" in final_content:
            import re
            logger.warning(
                "Content safety net: stripping leaked XML tool_call from final_content "
                "(session=%s, len=%d)", session_id, len(final_content),
            )
            final_content = re.sub(
                r'<tool_call>.*?</tool_call>', '', final_content, flags=re.DOTALL
            ).strip()
            if not final_content:
                final_content = "抱歉，我暂时无法处理这个消息，请稍后再试。"

        # 发送响应
        await self._reply.send_final(final_content, user_message)

        # 提取长期记忆（异步提交，不阻塞 Temporal Activity 完成）
        await self._memory.retain_memories(
            turn_events=turn_events,
            account_id=account_id,
            session_id=session_id,
            channel_type=channel_type_str,
            metadata=metadata,
        )

        # 每 10 轮输出一次可观测性指标快照（结构化日志，供监控采集）
        self._memory.maybe_log_metrics(turns_taken)

        return {
            "content": final_content,
            "turns": turns_taken,
            "session_id": session_id,
            "account_id": account_id,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # 内部方法
    # ═══════════════════════════════════════════════════════════════════════════

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
        *,
        remaining_turns: int = 0,
        max_tool_turns: int = 0,
        group_context_text: str = "",
    ) -> List[Dict[str, Any]]:
        """将事件历史 + 记忆组装为 LLM messages。

        remaining_turns / max_tool_turns 用于注入轮次限制提示，
        告知模型剩余可用轮次数以规划工具调用节奏。"""
        if self._ctx is None:
            return [{"role": "user", "content": ""}]
        return self._ctx.build(
            events=events,
            channel_type=channel_type,
            recalled_memories=memories_text,
            max_turns=20,
            remaining_turns=remaining_turns,
            max_tool_turns=max_tool_turns,
            group_context_text=group_context_text,
        )

    async def get_session_account(self, session_id: str) -> str:
        """Return the account id for a session, if it exists."""
        return await self._memory.get_session_account(session_id)

    async def archive_session(
        self, session_id: str, account_id: str,
    ) -> Dict[str, Any]:
        """完整归档流程：获取事件 → 写 history.jsonl → 删 WAL → 生成 meta 摘要。

        时序（防丢数据）:
          1. SessionStore.archive() — 返回全部事件
          2. write_history_jsonl()  — 落盘归档快照（永久真相源）
          3. delete_wal()           — 删除 WAL 文件（history.jsonl 已安全落盘）
          4. generate_session_summary()  — fast 模型摘要
          5. update_session_meta() — 写入 meta.yaml

        Returns:
            {"ok": bool, "task_summary": str, "tags": [...], "event_count": int}
        """
        from session.workspace import (
            write_history_jsonl, generate_session_summary, update_session_meta,
        )

        # 1. archive: 获取全部事件
        events = await self._memory.archive_events(session_id)
        if not events:
            logger.warning("Archive: no events for session %s", session_id)
            return {"ok": False, "error": "No events to archive"}

        # ── 群聊上下文：退订 + 获取归档快照 ──
        group_snapshot = None
        if self._group_context:
            try:
                # 从事件中提取 group_id（第一个 USER_MESSAGE 的 metadata）
                group_id = ""
                for e in events:
                    if e.get("event_type") == "user_message":
                        gid = e.get("metadata", {}).get("group_id", "")
                        if gid:
                            group_id = str(gid)
                        break
                if group_id:
                    # 获得快照
                    group_snapshot = await self._group_context.snapshot(group_id)
                    # 退订（SCARD 归零时自动清理 Redis）
                    remaining = await self._group_context.unsubscribe(group_id, session_id)
                    logger.info(
                        "Archive: group context unsubscribed group=%s session=%s remaining=%d",
                        group_id, session_id, remaining,
                    )
            except Exception:
                logger.warning("Archive: group context cleanup failed for %s", session_id)

        # 统计
        event_count = len(events)
        tool_calls = 0
        tools_used: set = set()
        for e in events:
            if e.get("event_type") == "model_message":
                for tc in e.get("content", {}).get("tool_calls", []):
                    tool_calls += 1
                    tools_used.add(tc.get("name", ""))

        # 2. 写 history.jsonl（先落盘，再继续）
        try:
            write_history_jsonl(
                self._file_store, account_id, session_id, events,
            )
        except Exception as e:
            logger.error("Archive: history.jsonl write failed for %s: %s", session_id, e)
            return {"ok": False, "error": f"history.jsonl write failed: {e}"}

        # ── 清理行动运行时的会话级缓存 ──
        self._actions.clear_session(session_id)

        # 3. 删除 WAL（history.jsonl 已安全落盘，WAL 使命完成）
        await self._memory.delete_wal(session_id)

        # 4. fast 模型生成摘要
        task_summary = ""
        tags: list[str] = []
        try:
            task_summary, tags = await generate_session_summary(
                events, self._model, self._prompts,
            )
        except Exception as e:
            logger.warning("Archive: summary generation failed for %s: %s", session_id, e)

        # 5. 更新 meta.yaml
        try:
            update_session_meta(
                self._file_store, account_id, session_id,
                status="completed",
                task_summary=task_summary,
                tags=list(tags),
                event_count=event_count,
                tool_calls=tool_calls,
                tools_used=sorted(tools_used),
                completed_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                **(dict(group_context=group_snapshot) if group_snapshot else {}),
            )
        except Exception as e:
            logger.warning("Archive: meta.yaml update failed for %s: %s", session_id, e)

        logger.info(
            "Archive complete: %s (%d events, %d tool calls, tags=%s)",
            session_id, event_count, tool_calls, tags,
        )
        return {
            "ok": True,
            "task_summary": task_summary,
            "tags": tags,
            "event_count": event_count,
            "tool_calls": tool_calls,
            "tools_used": sorted(tools_used),
        }

    async def reflect(self, account_id: str) -> Dict[str, Any]:
        """触发长期记忆深度推理。"""
        count = await self._memory.reflect(account_id)
        return {"insights": count}

    async def get_metrics(self) -> Dict[str, Any]:
        """返回 Hindsight 客户端可观测性指标快照。"""
        return await self._memory.get_metrics()


class HarnessRunner(TurnOrchestrator):
    """Backward-compatible name for TurnOrchestrator.

    Older modules and tests may still import HarnessRunner. New code should prefer
    TurnOrchestrator to make the architecture boundary explicit.
    """


__all__ = ["TurnOrchestrator", "HarnessRunner"]
