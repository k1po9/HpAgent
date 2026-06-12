"""
HarnessRunner —— 无状态协调器。

Harness 是 Temporal Activities 唯一的交互对象。
持有 SessionStore / ContextBuilder / ResourcePool / SandboxManager / ChannelRouter，
在 process_turn() 中协调完整的 agentic loop。

Temporal Workflow 只做编排（何时处理消息），Harness 做执行（如何调用模型、工具、记忆）。
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from common.types import (
    Event,
    EventType,
    ChannelType,
    UnifiedMessage,
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
    ):
        self._session = session_store
        self._ctx = context_builder
        self._model = resource_pool
        self._sandbox = sandbox_manager
        self._channel = channel_router
        self._group_context = group_context  # GroupContextStore | None
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
        # RAG 结果缓存: session_id → (query_hash, tools)，避免同 session 重复查询
        self._tools_cache: Dict[str, tuple] = {}
        # 工具进度提示（从 config/tool_hints.yaml 懒加载）
        self._tool_hints: Optional[Dict[str, str]] = None

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
        if self._sandbox is not None:
            try:
                sandbox = self._sandbox.get_sandbox_for_session(session_id)
                sandbox.reset_hints()
            except Exception:
                pass  # sandbox 尚未创建（新会话首轮），无 hints 需要清理

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
            # 召回长期记忆（仅在首轮执行一次，渠道感知的标签隔离）
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
                tools = await self._get_tools(user_content, session_id, group_context_text)

                # 调用模型
                # for turn in context:
                #     logger.info("Context turn: %s", turn)
                ch_overrides = self._channel_overrides.get(channel_type_str, {}) if self._channel_overrides else {}
                response = await self._model.generate(
                    model_selector="chat",
                    messages=context,
                    tools=tools if tools else None,
                    stream=ch_overrides.get("stream", False),
                    max_tokens=ch_overrides.get("max_tokens"),
                    latency_budget=ch_overrides.get("timeout"),
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

                    # 群聊低密度时发送工具进度提示
                    await self._maybe_send_tool_hints(
                        response.tool_calls, user_message,
                    )

                    for tc in response.tool_calls:
                        result = await self._execute_tool(tc.name, tc.arguments, session_id, user_query=user_content)
                        tool_event = Event(
                            session_id=session_id,
                            event_type=EventType.TOOL_RESULT,
                            content={
                                "tool_call_id": tc.id,
                                "tool_name": tc.name,
                                "result": result.get("summary", result.get("output")),
                                "error": result.get("error"),
                                "original_output": result.get("output"),
                                "metadata": result.get("metadata"),
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
                response = await self._model.generate(
                    model_selector="chat",
                    messages=context,
                    tools=None,
                    stream=ch_overrides.get("stream", False),
                    max_tokens=ch_overrides.get("max_tokens"),
                    latency_budget=ch_overrides.get("timeout"),
                )
                final_content = response.content or ""
                model_event = Event(
                    session_id=session_id,
                    event_type=EventType.MODEL_MESSAGE,
                    content={
                        "text": final_content,
                        "tool_calls": [],
                        "stop_reason": "forced_final",
                    },
                )
                events.append(model_event)
                await self._session.append_events(session_id, model_event)
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

    async def _get_tools(self, user_content: str = "", session_id: str = "", group_context_text: str = "") -> List[Dict[str, Any]]:
        """委托 Sandbox.select_tools() 完成完整工具选择管线。

        对同一 session 内相同 user_content 缓存结果，避免重复 embedding + RAG 查询。
        群聊时自动拼接最近 3 条群消息到 RAG 查询，提升工具检索相关性。"""

        # ── 拼接群聊上下文到 RAG 查询 ──
        # 格式: 用户问题在上，最近群聊消息分行展示。每条消息自带发送者标签
        # （如 "小刚: xxx"），embedding 模型能自然区分不同发言者。
        # 无关闲聊即使混入也不会匹配到股票工具，语义上自动淘汰。
        rag_query = user_content
        if group_context_text:
            recent_msgs = [m.strip() for m in group_context_text.strip().split("\n")[-3:] if m.strip()]
            if recent_msgs:
                rag_query = f"{user_content}\n--- 群聊 ---\n" + "\n".join(recent_msgs)
        if self._sandbox is None:
            return []
        try:
            # ── 缓存检查 ──
            if session_id:
                query_hash = hashlib.md5(user_content.encode()).hexdigest()
                if session_id in self._tools_cache:
                    last_hash, cached = self._tools_cache[session_id]
                    if query_hash == last_hash:
                        logger.info("_get_tools: cache hit session=%s", session_id)
                        return cached

            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            tools, audit = await sandbox.select_tools(rag_query, self._tool_rag_top_k)

            # ── 写入缓存 ──
            if session_id:
                self._tools_cache[session_id] = (query_hash, tools)

            logger.info("_get_tools: query=%s top_k=%d -> %d tools",
                       audit.get("queries", [rag_query])[0][:80], self._tool_rag_top_k, len(tools))

            # 记录审计事件
            if session_id:
                await self._session.append_events(session_id, Event(
                    session_id=session_id,
                    event_type=EventType.TOOL_RETRIEVAL,
                    content=audit,
                ))

            return tools
        except Exception as e:
            logger.warning("Tool retrieval failed for sid=%s: %s", session_id, e)
            return []

    # ── 群聊密度感知 → 工具进度提示 ──────────────────────────────────────
    # 仅群聊、低密度时发送，避免刷屏。
    # 工具提示从 config/tool_hints.yaml 加载；密度阈值从 config.yaml → RedisConfig 注入。

    def _load_tool_hints(self) -> Dict[str, str]:
        """从 config/mcp/servers.yaml 加载工具进度提示（每个 server 下 tools.<name>.hint）。
        向上遍历目录查找 config/，兼容宿主机和 Docker 不同的目录层级。"""
        hints_path = None
        p = Path(__file__).resolve().parent
        for _ in range(5):
            candidate = p / "config" / "mcp" / "servers.yaml"
            if candidate.exists():
                hints_path = candidate
                break
            p = p.parent
        if hints_path is None:
            logger.warning("Tool hints not found: config/mcp/servers.yaml")
            return {}
        try:
            with open(hints_path, encoding="utf-8") as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                return {}
            servers = raw.get("servers", {})
            hints = {}
            for server_name, server_cfg in servers.items():
                if not isinstance(server_cfg, dict):
                    continue
                tools = server_cfg.get("tools", {})
                if not isinstance(tools, dict):
                    continue
                for tool_name, tool_cfg in tools.items():
                    if isinstance(tool_cfg, dict) and isinstance(tool_cfg.get("hint"), str):
                        hints[tool_name] = tool_cfg["hint"]
            return hints
        except Exception:
            logger.warning("Failed to load tool hints from %s", hints_path)
        return {}

    def _get_tool_hint(self, tool_name: str) -> str:
        if self._tool_hints is None:
            self._tool_hints = self._load_tool_hints()
        return self._tool_hints.get(tool_name, "正在处理…")

    async def _maybe_send_tool_hints(
        self,
        tool_calls: list,
        user_message: Dict[str, Any],
    ) -> None:
        """群聊密度低时，发送工具调用进度提示，减少用户感知等待。

        只在群聊场景下生效；私聊不发（无需提示）。
        """
        metadata = user_message.get("metadata", {})
        if metadata.get("detail_type") != "group":
            return
        group_id = str(metadata.get("group_id", ""))
        if not group_id or not self._group_context:
            return

        # 密度检查：阈值来自 config
        try:
            density = await self._group_context.get_density(group_id)
            threshold = getattr(self._group_context, "density_threshold", 2.0)
            if density >= threshold:
                return  # 群聊热闹，不插话
        except Exception:
            return  # Redis 不可用时静默降级

        # 构造提示文本
        hints = []
        for tc in tool_calls:
            hint = self._get_tool_hint(tc.name)
            if hint and hint not in hints:
                hints.append(hint)

        progress = "，".join(hints[:3])  # 最多展示 3 个工具提示
        await self._send_response(progress, user_message)

    async def _execute_tool(
        self, tool_name: str, arguments: Dict[str, Any], session_id: str = "",
        user_query: str = "",
    ) -> Dict[str, Any]:
        """委托 Sandbox.execute() 执行工具（含截断），HarnessRunner 做可选摘要。"""
        if self._sandbox is None:
            return {"output": None, "error": "SandboxManager not configured"}
        try:
            sandbox = self._sandbox.get_sandbox_for_session(session_id)
            result, _audit = await sandbox.execute(tool_name, arguments)
            result_dict = result.to_dict()

            # 可选：对超长输出做 LLM 摘要（Sandbox 已做硬截断，这里做语义压缩）
            if self._tool_result_summary_enabled:
                result_dict = await self._summarize_if_needed(result_dict, tool_name, user_query, session_id)

            return result_dict
        except Exception as e:
            return {"output": None, "error": str(e)}

    async def _summarize_if_needed(
        self, result_dict: Dict[str, Any], tool_name: str, user_query: str = "",
        session_id: str = "",
    ) -> Dict[str, Any]:
        """工具输出超过阈值时的语义摘要。

        fast 模型当前环境不稳定（SiliconFlow / Alibaba 均频繁超时），
        直接走截断，不阻塞工具调用关键路径。
        后续有可靠 fast 模型时可将 _TOOL_SUMMARY_ENABLED 改回 True 并调低阈值。
        """
        _TOOL_SUMMARY_ENABLED = True

        output = result_dict.get("output")
        if not output or not isinstance(output, str):
            return result_dict
        if len(output) <= self._tool_result_summary_threshold:
            return result_dict
        if not _TOOL_SUMMARY_ENABLED or self._model is None or self._prompts is None:
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

            response = await self._model.generate(
                model_selector="fast",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                stream=False,
            )
            summary = (response.content or "").strip()
            if summary:
                logger.debug("Tool result summarized: %s %d->%d chars",
                           tool_name, len(output), len(summary))
                result_dict["summary"] = summary
                result_dict["metadata"] = result_dict.get("metadata") or {}
                result_dict["metadata"]["summarized"] = True
                if session_id:
                    await self._session.append_events(session_id, Event(
                        session_id=session_id,
                        event_type=EventType.TOOL_SUMMARY,
                        content={
                            "tool_name": tool_name,
                            "original_chars": len(output),
                            "summary_chars": len(summary),
                            "summary": summary,
                        },
                    ))
            else:
                result_dict["output"] = output[:self._tool_result_summary_max_chars]
        except Exception as e:
            logger.warning("Tool summary failed for %s: %s, falling back to truncation", tool_name, e)
            result_dict["output"] = output[:self._tool_result_summary_max_chars]

        return result_dict

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
        events = await self._session.archive(session_id)
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

        # ── 清理 RAG 缓存 ──
        self._tools_cache.pop(session_id, None)

        # 3. 删除 WAL（history.jsonl 已安全落盘，WAL 使命完成）
        await self._session.delete_wal(session_id)

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
        """通过 ChannelRouter 发送回复。

        群聊智能 @: 仅当同群内有多个活跃 session（多人同时在问 bot）
        时才在消息前加 [CQ:at,qq=xxx]，避免单人对话时多余的 @。
        """
        if self._channel is None:
            return False

        # ── 群聊智能 @ ──
        metadata = user_message.get("metadata", {})
        if (content
                and self._group_context
                and metadata.get("detail_type") == "group"):
            group_id = str(metadata.get("group_id", ""))
            if group_id:
                try:
                    subscribers = await self._group_context.subscriber_count(group_id)
                    if subscribers > 1:
                        sender_id = user_message.get("sender_id", "")
                        if sender_id:
                            content = f"[CQ:at,qq={sender_id}] {content}"
                            logger.debug(
                                "Smart @reply: group=%s subscribers=%d sender=%s",
                                group_id, subscribers, sender_id,
                            )
                except Exception:
                    pass  # Redis 不可用时静默降级

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
            ok = await self._channel.send(msg)
        except Exception as e:
            logger.warning("Channel send failed: %s", e)
            return False

        # ── 群聊：把自己发出的消息也写入窗口，让密度感知和 RAG 查询更准确 ──
        if ok and self._group_context and metadata.get("detail_type") == "group":
            group_id = str(metadata.get("group_id", ""))
            bot_id = metadata.get("self_id", "")
            if group_id and bot_id:
                try:
                    await self._group_context.append(
                        group_id=group_id,
                        sender_name="bot",
                        sender_id=bot_id,
                        content=content[:200],
                    )
                except Exception:
                    pass
        return ok
