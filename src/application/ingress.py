"""QQ trigger selection and UX; execution admission belongs only to PostgreSQL."""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import psycopg

from account.postgres_account_service import IdentityResolutionUnavailable
from application.conversation import (
    InvalidQQMessage,
    UnboundIdentity,
    group_event_id,
    normalize_qq_message,
)
from application.group_context_selection import SelectionPolicy, empty_snapshot, select_context
from common.types import UnifiedMessage
from web_domain.errors import IdempotencyConflict, ResourceNotFound

logger = logging.getLogger("HpAgent.MessageIngressService")
UNBOUND_REPLY = "账号尚未绑定，请先由管理员完成身份绑定后再使用。"
IDENTITY_RESOLUTION_UNAVAILABLE_REPLY = "服务暂时不可用，请稍后重试。"
BUSY_REPLY = "当前会话正在处理上一条消息，请稍后再试；可发送 /cancel 取消。"


class MessageIngressService:
    def __init__(self, group_context: Any = None, conversation_service: Any = None,
                 reply_service: Any = None, identity_command_service: Any = None, *,
                 context_policy: SelectionPolicy | None = None, context_timeout_seconds: float = .25):
        self._group_context = group_context
        self._conversation = conversation_service
        self._reply = reply_service
        self._identity_commands = identity_command_service
        self._context_policy = context_policy or SelectionPolicy()
        self._context_timeout = context_timeout_seconds

    async def handle(self, message: UnifiedMessage):
        if not message.content or not message.content.strip():
            return None
        channel = getattr(message.channel_type, "value", str(message.channel_type))
        try:
            source = normalize_qq_message(message, channel)
        except InvalidQQMessage:
            logger.warning("qq_invalid_protocol_message channel_type=%s", channel)
            return None
        # Only accepted delivery callbacks classify our own messages. Echoes
        # cannot establish whether a message was a final, control or progress.
        if message.sender_id == source.route['bot_id']:
            return None
        # A bounded cache is optional ambient context, never admission authority.
        # Trigger selection is independent of cache availability.
        window = empty_snapshot(source.event_id, status="degraded", reason="redis_disabled",
                                policy=self._context_policy)
        if source.route["scope"] in {"group", "guild"} and self._group_context:
            try:
                async with asyncio.timeout(self._context_timeout):
                    appended = await self._group_context.append(
                        source.context_key, event_id=source.event_id,
                        sender_name=message.metadata.get("sender_name", ""),
                        sender_id=message.sender_id, content=message.content,
                        message_type='user_message',
                        event_time=message.metadata.get("iso_timestamp") or message.metadata.get("timestamp", ""),
                        thread_id=source.route['thread_id'],
                        mention_ids=message.metadata.get('mention_ids', []),
                        reply_to_event_id=(group_event_id(source.route, message.metadata['reply_to_message_id'])
                                           if message.metadata.get('reply_to_message_id') else None),
                    )
                    if appended.status in {'conflict', 'degraded'}:
                        window['degraded_reason'] = appended.reason or appended.status
                    elif source.triggered:
                        read = await self._group_context.get_candidates(source.context_key)
                        if read.status == 'degraded':
                            window['degraded_reason'] = read.reason
                        else:
                            window = select_context(
                                read.messages, trigger_event_id=source.event_id, sender_id=message.sender_id,
                                thread_id=source.route['thread_id'], now=datetime.now(UTC),
                                reply_to_event_id=(group_event_id(source.route, message.metadata['reply_to_message_id'])
                                                   if message.metadata.get('reply_to_message_id') else None),
                                high_watermark=appended.sequence or None, policy=self._context_policy,
                            )
            except Exception as exc:
                window = empty_snapshot(source.event_id, status="degraded", reason=type(exc).__name__,
                                        policy=self._context_policy)
            logger.info("qq_group_context_selected status=%s candidates=%d selected=%d tokens=%d reason=%s",
                        window['status'], window['candidate_count'], window['selected_count'],
                        window['estimated_tokens'], window['degraded_reason'])
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
                if result.body.get('work_control'):
                    work = result.body['work_control']
                    await self._send(f"Work {work['work_id']}: {work['status']} (r{work['requirement_revision']}, {work['reason']})",message,channel)
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
