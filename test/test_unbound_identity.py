"""
Phase F 未绑定身份拒绝（用户明确要求，doc §17）。

契约:
  - PostgresAccountService.resolve 返回 None 时，
    ConversationService.start_or_signal 必须抛 UnboundIdentity。
  - 绝不回落到 accounts.json，也绝不自动创建 Account / IdentityBinding。
  - 消息不进入 Temporal / Agent / Session / Hindsight。
  - MessageIngressService 捕获后: 记录结构化 qq_identity_unbound 告警，
    向 QQ 发送固定 "账号尚未绑定" 回复（走 ReplyService / ChannelRouter）。
  - bootstrap_identity 创建绑定后，后续消息恢复正常（resolve 命中）。
"""
import logging
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from application.conversation import ConversationService, UnboundIdentity
from application.ingress import UNBOUND_REPLY, MessageIngressService
from common.types import ChannelType, UnifiedMessage


class _NoopAccountService:
    """PostgresAccountService 的替身：无绑定 → 返回 None（绝不自动创建）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def resolve(self, channel_type: str, channel_user_id: str) -> str | None:
        self.calls.append((channel_type, channel_user_id))
        return None


class _BoundAccountService(_NoopAccountService):
    async def resolve(self, channel_type: str, channel_user_id: str) -> str | None:
        self.calls.append((channel_type, channel_user_id))
        return "account-A"


class _NeverCalledTemporal:
    def __init__(self) -> None:
        self.started = False
        self.signaled = False

    async def start_workflow(self, *args, **kwargs):
        self.started = True
        return None

    def get_workflow_handle(self, workflow_id):
        self.signaled = True
        return object()


def _conversation_service(account_service) -> ConversationService:
    return ConversationService(
        temporal_client=_NeverCalledTemporal(),
        workflow_cls=object,
        task_queue="tq",
        idle_timeout_minutes=10,
        activity_timeout=60,
        account_service=account_service,
        workspace_root=Path("/tmp"),
        file_store=object(),
        workspace_db=object(),
        git_repo_manager=object(),
        sandbox_manager=object(),
    )


def _message(sender_id: str = "sender", content: str = "hello") -> UnifiedMessage:
    return UnifiedMessage(
        message_id="m1",
        sender_id=sender_id,
        channel_type=ChannelType.NAPCAT,
        content=content,
        timestamp=1.0,
        metadata={"detail_type": "private"},
    )


@pytest.mark.asyncio
async def test_conversation_service_raises_unbound_identity_on_none():
    service = _conversation_service(_NoopAccountService())
    with pytest.raises(UnboundIdentity) as excinfo:
        await service.start_or_signal(_message(), "napcat")
    assert excinfo.value.channel_type == "napcat"
    assert excinfo.value.sender_id == "sender"


@pytest.mark.asyncio
async def test_unbound_never_reaches_temporal():
    account_service = _NoopAccountService()
    temporal = _NeverCalledTemporal()
    service = ConversationService(
        temporal_client=temporal,
        workflow_cls=object,
        task_queue="tq",
        idle_timeout_minutes=10,
        activity_timeout=60,
        account_service=account_service,
        workspace_root=Path("/tmp"),
        file_store=object(),
        workspace_db=object(),
        git_repo_manager=object(),
        sandbox_manager=object(),
    )
    with pytest.raises(UnboundIdentity):
        await service.start_or_signal(_message(), "napcat")
    assert temporal.started is False
    assert temporal.signaled is False


class _RaisingConversation:
    def __init__(self) -> None:
        self.calls = 0

    async def start_or_signal(self, message: UnifiedMessage, channel_type: str):
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

    # 拒绝路径只调用一次 start_or_signal，绝不回落 accounts.json。
    assert conversation.calls == 1
    assert len(reply.sent) == 1
    content, user_message = reply.sent[0]
    assert content == UNBOUND_REPLY
    assert user_message["sender_id"] == "qq-user"
    assert user_message["channel_type"] == "napcat"
    assert user_message["session_id"] == ""
    assert user_message["account_id"] == ""


@pytest.mark.asyncio
async def test_bound_identity_returns_to_normal_execution():
    """bootstrap 建立绑定后，resolve 命中，ConversationService 越过身份拒绝。

    绑定命中 → start_or_signal 在 resolve 处不再抛 UnboundIdentity；其后的
    资源准备失败会被 ConversationService 内部 try/except 吞掉（与本测试无关，
    身份链路已经放行）。
    """
    account_service = _BoundAccountService()
    service = _conversation_service(account_service)
    await service.start_or_signal(_message(), "napcat")
    assert account_service.calls == [("napcat", "sender")]
