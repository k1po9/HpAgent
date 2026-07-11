"""MessageIngressService —— 入站消息应用服务。

职责：
  - 过滤空消息
  - 维护群聊短期上下文
  - 对群聊非 @bot 消息只沉淀上下文，不触发 Agent
  - 将需要处理的消息交给 ConversationService
"""
from __future__ import annotations

import logging
from typing import Any

from common.types import UnifiedMessage

logger = logging.getLogger("HpAgent.MessageIngressService")


class MessageIngressService:
    """消息入口服务，让 worker.py 不再承载入站业务规则。"""

    def __init__(self, group_context: Any = None, conversation_service: Any = None):
        self._group_context = group_context
        self._conversation = conversation_service

    async def handle(self, message: UnifiedMessage) -> None:
        if not message.content or not message.content.strip():
            return

        ch_type = (
            message.channel_type.value
            if hasattr(message.channel_type, "value")
            else str(message.channel_type)
        )

        should_continue = await self._capture_group_context(message)
        if not should_continue:
            return

        await self._conversation.start_or_signal(message, ch_type)

    async def _capture_group_context(self, message: UnifiedMessage) -> bool:
        """写入群聊上下文；非 @bot 群消息返回 False。"""
        metadata = message.metadata
        detail_type = metadata.get("detail_type", "")
        group_id = str(metadata.get("group_id", ""))
        is_at_bot = metadata.get("is_at_bot", False)

        if detail_type != "group" or not group_id or not self._group_context:
            return True

        sender_name = metadata.get("sender_name", "")
        sender_id = message.sender_id
        raw_msg_id = metadata.get("message_id")
        msg_id = str(raw_msg_id) if raw_msg_id is not None else ""
        iso_ts = metadata.get("iso_timestamp", "")

        try:
            await self._group_context.append(
                group_id=group_id,
                sender_name=sender_name,
                sender_id=sender_id,
                content=message.content,
                msg_id=msg_id,
                timestamp=iso_ts,
            )
        except Exception:
            logger.warning("Failed to append group context for group %s", group_id)

        if not is_at_bot:
            logger.debug(
                "Group non-@ message from %s in %s (len=%d) -> context only, skipped",
                sender_id, group_id, len(message.content),
            )
            return False

        return True

