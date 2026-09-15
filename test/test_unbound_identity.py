"""QQ canonical command authorization and infrastructure failure UX."""
import logging
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from account.postgres_account_service import (
    IdentityResolutionUnavailable,
    PostgresAccountService,
)
from application.conversation import ConversationService, UnboundIdentity
from application.ingress import (
    IDENTITY_RESOLUTION_UNAVAILABLE_REPLY,
    UNBOUND_REPLY,
    MessageIngressService,
)
from common.types import ChannelType, UnifiedMessage


class Commands:
    def execute(self, **kwargs):
        from conversation_domain.surface_commands import IdentityNotBound
        raise IdentityNotBound()


def _message(sender_id="sender", content="hello"):
    return UnifiedMessage(sender_id=sender_id, channel_type=ChannelType.NAPCAT, content=content,
                          metadata={"detail_type": "private", "self_id": "bot", "message_id": "1"})


@pytest.mark.asyncio
async def test_conversation_service_maps_canonical_unbound_identity():
    with pytest.raises(UnboundIdentity) as error:
        await ConversationService(Commands()).accept(_message(), "napcat")
    assert error.value.channel_type == "napcat" and error.value.sender_id == "sender"


class _RaisingConversation:
    def __init__(self) -> None:
        self.calls = 0

    async def accept(self, message: UnifiedMessage, channel_type: str, **kwargs):
        self.calls += 1
        raise UnboundIdentity(channel_type=channel_type, sender_id=message.sender_id)


class _FakeReply:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []

    async def send_final(self, content: str, user_message: dict) -> bool:
        self.sent.append((content, user_message))
        return True


@pytest.mark.asyncio
async def test_ingress_sends_fixed_unbound_reply_and_logs_structured_warning(caplog):
    conversation = _RaisingConversation()
    reply = _FakeReply()
    ingress = MessageIngressService(
        group_context=None,
        conversation_service=conversation,
        reply_service=reply,
    )
    with caplog.at_level(logging.WARNING, logger="HpAgent.MessageIngressService"):
        await ingress.handle(_message(sender_id="qq-user"))

    assert "qq_identity_unbound" in caplog.text
    assert "channel_type=napcat" in caplog.text
    assert "sender_id=qq-user" in caplog.text

    # 拒绝路径只调用一次 accept，绝不回落 accounts.json。
    assert conversation.calls == 1
    assert len(reply.sent) == 1
    content, user_message = reply.sent[0]
    assert content == UNBOUND_REPLY
    assert user_message["sender_id"] == "qq-user"
    assert user_message["channel_type"] == "napcat"
    assert user_message["session_id"] == ""
    assert user_message["account_id"] == ""


class _RaisingUnavailableConversation:
    """ConversationService 的替身：身份解析基础设施故障（DB 挂）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def accept(self, message: UnifiedMessage, channel_type: str, **kwargs):
        self.calls += 1
        raise IdentityResolutionUnavailable(
            provider="qq",
            normalized_subject=f"{channel_type}:{message.sender_id}",
        )


@pytest.mark.asyncio
async def test_ingress_replies_unavailable_not_unbound_on_resolution_failure(caplog):
    """DB 故障 ≠ 未绑定：回复"服务暂时不可用"，绝不说"账号尚未绑定"。"""
    conversation = _RaisingUnavailableConversation()
    reply = _FakeReply()
    ingress = MessageIngressService(
        group_context=None,
        conversation_service=conversation,
        reply_service=reply,
    )
    with caplog.at_level(logging.WARNING, logger="HpAgent.MessageIngressService"):
        await ingress.handle(_message(sender_id="qq-user"))

    assert "qq_database_unavailable" in caplog.text

    assert conversation.calls == 1
    assert len(reply.sent) == 1
    content, user_message = reply.sent[0]
    assert content == IDENTITY_RESOLUTION_UNAVAILABLE_REPLY
    assert content != UNBOUND_REPLY
    assert user_message["sender_id"] == "qq-user"


@pytest.mark.asyncio
async def test_postgres_service_raises_unavailable_not_none_on_db_failure():
    """SELECT 失败 → IdentityResolutionUnavailable（绝不伪装成 None / 未绑定）。

    DSN 指向不可达端口（连接拒绝），psycopg 抛错 → 服务重抛
    IdentityResolutionUnavailable，由 ingress 层翻译成"服务暂时不可用"。
    """
    service = PostgresAccountService("postgresql://nobody@127.0.0.1:1/hpagent")
    with pytest.raises(IdentityResolutionUnavailable) as excinfo:
        await service.resolve("napcat", "10001")
    assert excinfo.value.provider == "qq"
    assert excinfo.value.normalized_subject == "napcat:10001"


@pytest.mark.asyncio
async def test_bound_identity_returns_canonical_command_result():
    class BoundCommands:
        def execute(self, **kwargs):
            assert kwargs["provider"] == "qq" and kwargs["subject"] == "napcat:sender"
            return "accepted"
    assert await ConversationService(BoundCommands()).accept(_message(), "napcat") == "accepted"
