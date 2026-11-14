from __future__ import annotations

import pytest

from application.reply import ReplyService


def _group_message():
    return {
        "message_id": "qq-message",
        "session_id": "session",
        "account_id": "account",
        "sender_id": "sender",
        "channel_type": "onebot11",
        "content": "question",
        "metadata": {
            "detail_type": "group",
            "group_id": "group",
            "self_id": "bot-id",
        },
    }


@pytest.mark.asyncio
async def test_control_reply_preserves_router_without_context_or_smart_mention():
    sent = []

    class Router:
        async def send(self, message):
            sent.append(message)
            return True

    reply = ReplyService(Router())
    message = _group_message()
    assert await reply.send_final("answer", message)
    assert [item.content for item in sent] == ["answer"]
    assert "mention_sender" not in sent[0].metadata
    assert sent[0].metadata == message['metadata']
    assert not hasattr(reply, 'send_progress')


@pytest.mark.asyncio
async def test_ae_014_qq_channel_failure_does_not_raise_from_reply_service():
    class BrokenRouter:
        async def send(self, message):
            raise ConnectionError("channel unavailable")

    reply = ReplyService(BrokenRouter())
    message = _group_message()
    message["metadata"] = {"detail_type": "private"}
    assert await reply.send_final("computed result", message) is False
