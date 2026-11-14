"""Best-effort ingress control replies; final Agent results use durable Delivery."""
from __future__ import annotations

import logging
from typing import Any

from channels.router import ChannelRouter
from common.types import ChannelType, UnifiedMessage

logger = logging.getLogger("HpAgent.ReplyService")


class ReplyService:
    def __init__(self, channel_router: ChannelRouter | None = None):
        self._channel = channel_router

    async def send_final(self, content: str, user_message: dict[str, Any]) -> bool:
        if self._channel is None:
            return False
        try:
            return await self._channel.send(UnifiedMessage(
                session_id=user_message['session_id'], account_id=user_message['account_id'],
                sender_id=user_message['sender_id'],
                channel_type=ChannelType(user_message.get('channel_type', 'console')),
                content=content, metadata=dict(user_message.get('metadata', {})),
            ))
        except Exception:
            logger.warning('qq_control_reply_failed')
            return False
