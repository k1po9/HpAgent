from __future__ import annotations

from types import SimpleNamespace

import pytest

from account.identity import (
    normalize_channel_subject,
    normalize_qq_subject,
    normalize_web_subject,
)
from account.identity_binding_service import mask_qq_subject
from application.identity_commands import IdentityCommandService
from application.ingress import MessageIngressService
from common.types import ChannelType, UnifiedMessage


class _Bindings:
    def __init__(self):
        self.calls = []

    def consume_qq_challenge(self, code, channel_type, sender_id):
        self.calls.append((code, channel_type, sender_id))
        return SimpleNamespace(consolidated=False)


class _Conversation:
    def __init__(self):
        self.calls = []

    async def start_or_signal(self, *args):
        self.calls.append(args)


class _Reply:
    def __init__(self):
        self.calls = []

    async def send_final(self, content, message):
        self.calls.append((content, message))


def test_identity_normalization_is_shared_and_channel_scoped():
    assert normalize_web_subject(" HuangPei ") == "huangpei"
    assert normalize_channel_subject("web", " HUANGPEI ") == ("web", "huangpei")
    assert normalize_qq_subject("napcat", " 123456 ") == "napcat:123456"
    assert normalize_qq_subject("official_qq", "openid") == "official_qq:openid"
    assert normalize_qq_subject("web", "123") is None


def test_qq_mask_does_not_expose_full_subject():
    assert mask_qq_subject("123456789") == "123***789"
    assert mask_qq_subject("1234") == "****"


@pytest.mark.asyncio
async def test_binding_command_is_consumed_before_conversation_or_agent_path():
    bindings = _Bindings()
    conversation = _Conversation()
    reply = _Reply()
    ingress = MessageIngressService(
        conversation_service=conversation,
        reply_service=reply,
        identity_command_service=IdentityCommandService(bindings),
    )
    message = UnifiedMessage(
        sender_id="123456",
        content="绑定 hp-483921",
        channel_type=ChannelType.NAPCAT,
    )

    await ingress.handle(message)

    assert bindings.calls == [("HP-483921", "napcat", "123456")]
    assert conversation.calls == []
    assert reply.calls[0][0] == "QQ 绑定成功。"


@pytest.mark.asyncio
async def test_non_binding_message_keeps_existing_ingress_path():
    bindings = _Bindings()
    conversation = _Conversation()
    ingress = MessageIngressService(
        conversation_service=conversation,
        identity_command_service=IdentityCommandService(bindings),
    )
    message = UnifiedMessage(
        sender_id="123456", content="你好", channel_type=ChannelType.NAPCAT
    )

    await ingress.handle(message)

    assert bindings.calls == []
    assert len(conversation.calls) == 1
