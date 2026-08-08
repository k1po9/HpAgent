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

from account.postgres_account_service import IdentityResolutionUnavailable
from application.conversation import UnboundIdentity

logger = logging.getLogger("HpAgent.MessageIngressService")

# Phase F：无活跃身份绑定的发送者收到的固定用户可见回复。
UNBOUND_REPLY = "账号尚未绑定，请先由管理员完成身份绑定后再使用。"

# Phase F：身份解析基础设施不可用（DB 故障）≠ 未绑定，绝不回复"账号尚未绑定"。
IDENTITY_RESOLUTION_UNAVAILABLE_REPLY = "服务暂时不可用，请稍后重试。"


class MessageIngressService:
    """消息入口服务，让 worker.py 不再承载入站业务规则。"""

    def __init__(
        self,
        group_context: Any = None,
        conversation_service: Any = None,
        reply_service: Any = None,
    ):
        self._group_context = group_context
        self._conversation = conversation_service
        self._reply = reply_service

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

        try:
            await self._conversation.start_or_signal(message, ch_type)
        except UnboundIdentity:
            await self._reject_unbound(message, ch_type)
        except IdentityResolutionUnavailable as exc:
            await self._reject_resolution_unavailable(message, ch_type, exc)

    async def _reject_unbound(self, message: UnifiedMessage, ch_type: str) -> None:
        """Phase F：无身份绑定 → 结构化告警 + 固定回复，绝不自动建号。

        发送者没有活跃的 PostgreSQL identity binding：消息不进入
        Temporal / Agent / Session / Hindsight。回复走 ReplyService /
        ChannelRouter（含群聊智能 @），与正常回复同一路径。
        """
        logger.warning(
            "qq_identity_unbound channel_type=%s sender_id=%s",
            ch_type, message.sender_id,
        )
        if self._reply is None:
            return
        try:
            await self._reply.send_final(
                UNBOUND_REPLY, self._build_user_message(message, ch_type)
            )
        except Exception as e:
            logger.warning("Unbound reply send failed: %s", e)

    async def _reject_resolution_unavailable(
        self, message: UnifiedMessage, ch_type: str, exc: IdentityResolutionUnavailable
    ) -> None:
        """Phase F：DB 故障 ≠ 没绑定。基础设施错误回复"服务暂时不可用"。

        PostgresAccountService 只在 SELECT 本身失败时抛
        IdentityResolutionUnavailable；业务上确实没有 binding 仍走
        UnboundIdentity（"账号尚未绑定"）。绝不把基础设施错误伪装成业务
        未绑定，也绝不自动创建账号。
        """
        logger.warning(
            "qq_identity_resolution_unavailable channel_type=%s sender_id=%s provider=%s",
            ch_type, message.sender_id, exc.provider,
        )
        if self._reply is None:
            return
        try:
            await self._reply.send_final(
                IDENTITY_RESOLUTION_UNAVAILABLE_REPLY,
                self._build_user_message(message, ch_type),
            )
        except Exception as e:
            logger.warning("Unavailable reply send failed: %s", e)

    @staticmethod
    def _build_user_message(message: UnifiedMessage, ch_type: str) -> dict:
        """构造投递给 ReplyService 的合成 user_message（与正常回复同一路径）。"""
        return {
            "message_id": message.message_id,
            "content": message.content,
            "sender_id": message.sender_id,
            "channel_type": ch_type,
            "session_id": "",
            "account_id": "",
            "metadata": message.metadata,
            "timestamp": message.timestamp,
        }

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

