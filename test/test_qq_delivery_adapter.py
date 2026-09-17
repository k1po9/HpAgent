import asyncio
from uuid import uuid4

import pytest

from application.qq_delivery import QQDeliveryAdapter
from channels.napcat import NapCatChannel
from common.types import ChannelType, UnifiedMessage


def test_qq_parts_quote_mention_and_protected_attachment_references():
    row = {
        "payload": {
            "content": "[CQ:at,qq=everyone]" + "x" * 1600,
            "files": ["file-id"],
            "origin": {
                "channel_type": "napcat",
                "scope": "group",
                "sender_id": "123",
                "external_message_id": "42",
            },
        }
    }
    parts = QQDeliveryAdapter(None).parts(row)
    assert len(parts) == 2
    assert parts[0].startswith("[CQ:reply,id=42][CQ:at,qq=123] ")
    assert "&#91;CQ:at" in parts[0]
    assert "/api/v1/files/file-id/content" in parts[1]


@pytest.mark.asyncio
async def test_napcat_delivery_waits_for_ack_and_selects_bot():
    channel = NapCatChannel.__new__(NapCatChannel)
    channel._delivery_acks = {}
    sent = []

    class Socket:
        async def send(self, payload):
            sent.append(payload)
            await asyncio.sleep(0)
            next(iter(channel._delivery_acks.values())).set_result(True)

    chosen, other = Socket(), Socket()
    channel._connected_clients = {chosen, other}
    channel._client_bots = {chosen: "bot", other: "other"}
    message = UnifiedMessage(
        sender_id="123",
        channel_type=ChannelType.NAPCAT,
        content="answer",
        metadata={
            "detail_type": "private",
            "self_id": "bot",
            "delivery_id": str(uuid4()),
            "msg_seq": 1,
        },
    )
    assert await channel.send_message(message)
    assert len(sent) == 1
    assert not channel._delivery_acks


@pytest.mark.asyncio
async def test_official_failure_does_not_suppress_identical_retry():
    from channels.official_qq import OfficialQQChannel

    channel = OfficialQQChannel.__new__(OfficialQQChannel)
    channel._sent_msg_ids = {}
    sent = []

    async def no_wait():
        return None

    async def token():
        return "test-token"

    class Response:
        def __init__(self, status):
            self.status = status

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def text(self):
            return "rejected"

    class Session:
        def post(self, url, **kwargs):
            sent.append(kwargs["json"].copy())
            return Response(400 if len(sent) == 1 else 200)

    channel._rate_limit = no_wait
    channel._get_token = token
    channel._session = Session()
    channel._build_send_payload = lambda message, detail: (
        "https://example.invalid",
        {"content": message.content},
    )
    channel._clean_dedup_cache = lambda now: None
    message = UnifiedMessage(
        sender_id="123",
        channel_type=ChannelType.OFFICIAL_QQ,
        content="answer",
        metadata={
            "detail_type": "private",
            "delivery_id": str(uuid4()),
            "msg_id": "external",
            "msg_seq": 1,
        },
    )
    assert not await channel.send_message(message)
    assert await channel.send_message(message)
    assert sent[0] == sent[1]
