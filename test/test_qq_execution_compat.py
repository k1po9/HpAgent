from __future__ import annotations

import pytest

from application.reply import ReplyService


def _group_message():
    return {
        "message_id": "qq-message",
        "session_id": "session",
        "account_id": "account",
        "sender_id": "sender",
        "channel_type": "napcat",
        "content": "question",
        "metadata": {
            "detail_type": "group",
            "group_id": "group",
            "self_id": "bot-id",
        },
    }


@pytest.mark.asyncio
async def test_ae_012_013_qq_final_and_progress_preserve_router_behavior():
    sent = []
    appended = []

    class Router:
        async def send(self, message):
            sent.append(message)
            return True

    class GroupContext:
        density_threshold = 2.0

        async def get_density(self, group_id):
            return 0.1

        async def subscriber_count(self, group_id):
            return 2

        async def append(self, **kwargs):
            appended.append(kwargs)

    class Prompts:
        bot_name = "agent"

    reply = ReplyService(Router(), GroupContext(), Prompts())
    reply._tool_hints = {"search": "正在搜索"}
    message = _group_message()

    from types import SimpleNamespace

    await reply.send_progress([SimpleNamespace(name="search")], message)
    await reply.send_final("answer", message)

    assert [item.content for item in sent] == [
        "[CQ:at,qq=sender] 正在搜索",
        "[CQ:at,qq=sender] answer",
    ]
    assert [item["content"] for item in appended] == [
        "[CQ:at,qq=sender] 正在搜索",
        "[CQ:at,qq=sender] answer",
    ]


@pytest.mark.asyncio
async def test_ae_014_qq_channel_failure_does_not_raise_from_reply_service():
    class BrokenRouter:
        async def send(self, message):
            raise ConnectionError("channel unavailable")

    reply = ReplyService(BrokenRouter())
    message = _group_message()
    message["metadata"] = {"detail_type": "private"}
    assert await reply.send_final("computed result", message) is False
