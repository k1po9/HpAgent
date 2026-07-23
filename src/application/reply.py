"""ReplyService —— 统一负责对外发送回复和工具进度提示。

这层把“怎么说出去”的渠道细节从 TurnOrchestrator 中剥离出来：
  - 最终回复发送
  - 群聊智能 @
  - 低密度群聊中的工具进度提示
  - bot 自己发出的消息写回群聊短期上下文
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from common.types import ChannelType, UnifiedMessage
from channels.router import ChannelRouter

logger = logging.getLogger("HpAgent.ReplyService")


class ReplyService:
    """回复发送应用服务。

    TurnOrchestrator/TurnOrchestrator 只需要交付内容和 user_message，
    本服务负责渠道路由、群聊 @ 策略和工具进度提示。
    """

    def __init__(
        self,
        channel_router: Optional[ChannelRouter] = None,
        group_context: Any = None,
        prompts: Any = None,
    ):
        self._channel = channel_router
        self._group_context = group_context
        self._prompts = prompts
        self._tool_hints: Optional[Dict[str, str]] = None

    async def send_final(self, content: str, user_message: Dict[str, Any]) -> bool:
        """发送最终回复。"""
        return await self._send(content, user_message)

    async def send_progress(self, tool_calls: list, user_message: Dict[str, Any]) -> bool:
        """群聊低密度时发送工具调用进度提示，减少用户感知等待。"""
        metadata = user_message.get("metadata", {})
        if metadata.get("detail_type") != "group":
            return False

        group_id = str(metadata.get("group_id", ""))
        if not group_id or not self._group_context:
            return False

        try:
            density = await self._group_context.get_density(group_id)
            threshold = getattr(self._group_context, "density_threshold", 2.0)
            if density >= threshold:
                return False
        except Exception:
            return False

        hints = []
        for tc in tool_calls:
            hint = self._get_tool_hint(tc.name)
            if hint and hint not in hints:
                hints.append(hint)

        progress = "，".join(hints[:3])
        if not progress:
            return False
        return await self._send(progress, user_message)

    async def _send(self, content: str, user_message: Dict[str, Any]) -> bool:
        if self._channel is None:
            return False

        metadata = user_message.get("metadata", {})
        content = await self._format_group_mention(content, user_message, metadata)

        ch_type = self._resolve_channel(user_message.get("channel_type", "console"))
        msg = UnifiedMessage(
            session_id=user_message["session_id"],
            account_id=user_message["account_id"],
            sender_id=user_message["sender_id"],
            channel_type=ch_type,
            content=content,
            metadata=metadata,
        )

        try:
            ok = await self._channel.send(msg)
        except Exception as e:
            logger.warning("Channel send failed: %s", e)
            return False

        if ok:
            await self._append_bot_group_context(content, metadata)
        return ok

    async def _format_group_mention(
        self,
        content: str,
        user_message: Dict[str, Any],
        metadata: Dict[str, Any],
    ) -> str:
        """群聊智能 @：多人同时问 bot 时才 @ 原用户。"""
        if not content or not self._group_context or metadata.get("detail_type") != "group":
            return content

        group_id = str(metadata.get("group_id", ""))
        if not group_id:
            return content

        try:
            subscribers = await self._group_context.subscriber_count(group_id)
            if subscribers > 1:
                sender_id = user_message.get("sender_id", "")
                if sender_id:
                    logger.debug(
                        "Smart @reply: group=%s subscribers=%d sender=%s",
                        group_id, subscribers, sender_id,
                    )
                    return f"[CQ:at,qq={sender_id}] {content}"
        except Exception:
            pass
        return content

    async def _append_bot_group_context(self, content: str, metadata: Dict[str, Any]) -> None:
        """把 bot 自己发出的群聊消息写入窗口，供密度感知和 RAG 查询使用。"""
        if not self._group_context or metadata.get("detail_type") != "group":
            return

        group_id = str(metadata.get("group_id", ""))
        bot_id = metadata.get("self_id", "")
        if not group_id or not bot_id:
            return

        try:
            await self._group_context.append(
                group_id=group_id,
                sender_name=getattr(self._prompts, "bot_name", "bot"),
                sender_id=bot_id,
                content=content[:200],
            )
        except Exception:
            pass

    def _load_tool_hints(self) -> Dict[str, str]:
        """从 config/mcp/servers.yaml 加载工具进度提示。"""
        hints_path = None
        p = Path(__file__).resolve().parent
        for _ in range(6):
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
            for server_cfg in servers.values():
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

    def _resolve_channel(self, raw: str) -> Optional[ChannelType]:
        try:
            return ChannelType(raw)
        except ValueError:
            return None
