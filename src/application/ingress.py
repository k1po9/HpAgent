"""QQ trigger selection and UX; execution admission belongs only to PostgreSQL."""
from __future__ import annotations

import logging
from typing import Any

import psycopg

from account.postgres_account_service import IdentityResolutionUnavailable
from application.conversation import InvalidQQMessage, UnboundIdentity, normalize_qq_message
from common.types import UnifiedMessage
from web_domain.errors import IdempotencyConflict, ResourceNotFound

logger = logging.getLogger("HpAgent.MessageIngressService")
UNBOUND_REPLY = "账号尚未绑定，请先由管理员完成身份绑定后再使用。"
IDENTITY_RESOLUTION_UNAVAILABLE_REPLY = "服务暂时不可用，请稍后重试。"
BUSY_REPLY = "当前会话正在处理上一条消息，请稍后再试；可发送 /cancel 取消。"


class MessageIngressService:
    def __init__(self, group_context: Any = None, conversation_service: Any = None,
                 reply_service: Any = None, identity_command_service: Any = None):
        self._group_context = group_context
        self._conversation = conversation_service
        self._reply = reply_service
        self._identity_commands = identity_command_service

    async def handle(self, message: UnifiedMessage):
        if not message.content or not message.content.strip():
            return None
        channel = getattr(message.channel_type, "value", str(message.channel_type))
        try:
            source = normalize_qq_message(message, channel)
        except InvalidQQMessage:
            logger.warning("qq_invalid_protocol_message channel_type=%s", channel)
            return None
        # A bounded cache is optional ambient context, never admission authority.
        # Trigger selection is independent of cache availability.
        window = ""
        if source.route["scope"] in {"group", "guild"} and self._group_context:
            try:
                await self._group_context.append(
                    group_id=source.context_key, sender_name=message.metadata.get("sender_name", ""),
                    sender_id=message.sender_id, content=message.content,
                    msg_id=source.message_key, timestamp=message.metadata.get("iso_timestamp", ""),
                )
                if source.triggered:
                    # Freeze source IDs with selected text; execution never rereads Redis.
                    items = await self._group_context.get_window_json(source.context_key)
                    window = "\n".join(
                        f"[{item.get('msg_id', '')}] {item.get('sender_id', '')}: {item.get('content', '')}"
                        for item in items
                    )
            except Exception:
                logger.warning("qq_group_context_degraded context_key=%s", source.context_key)
        if not source.triggered:
            return None
        try:
            if self._identity_commands:
                command = await self._identity_commands.try_handle(message, channel)
                if command.handled:
                    if command.reply:
                        await self._send(command.reply, message, channel)
                    return command
            result = await self._conversation.accept(message, channel, envelope=source, group_context=window)
            if result is not None:
                code = result.body.get("code")
                if code == "conversation_busy":
                    await self._send(BUSY_REPLY, message, channel)
                elif code in {"no_matching_run", "run_not_cancellable"}:
                    await self._send("当前会话没有可取消的任务。", message, channel)
                elif result.body.get("status") in {"cancelled", "cancelling"}:
                    await self._send("已提交取消。", message, channel)
            return result
        except UnboundIdentity:
            logger.warning("qq_identity_unbound channel_type=%s sender_id=%s", channel, message.sender_id)
            await self._send(UNBOUND_REPLY, message, channel)
        except (IdentityResolutionUnavailable, psycopg.Error):
            logger.warning("qq_database_unavailable channel_type=%s", channel)
            await self._send(IDENTITY_RESOLUTION_UNAVAILABLE_REPLY, message, channel)
        except (IdempotencyConflict, InvalidQQMessage):
            await self._send("消息标识冲突或命令无效，请重新发送。", message, channel)
        except ResourceNotFound:
            await self._send("会话不可用或无权访问。", message, channel)
        return None

    async def _send(self, content: str, message: UnifiedMessage, channel: str):
        if self._reply:
            try:
                await self._reply.send_final(content, self._build_user_message(message, channel))
            except Exception:
                # Delivery failure does not roll back/reexecute a committed command.
                logger.warning("qq_control_reply_failed channel_type=%s", channel)

    @staticmethod
    def _build_user_message(message: UnifiedMessage, channel: str) -> dict:
        return {"message_id": message.message_id, "content": message.content,
                "sender_id": message.sender_id, "channel_type": channel,
                "session_id": "", "account_id": "", "metadata": message.metadata,
                "timestamp": message.timestamp}
