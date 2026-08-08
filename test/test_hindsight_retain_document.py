"""
retain_document —— Phase F 统一 Retain API 单元测试（doc §21-27）。

覆盖:
  ④ retain_document 提交显式 document_id，且不随重复调用改变
  ⑤ 同一 source 重复 retain 保持同一个 document_id（幂等核心）
  - 空 content / 空 document_id → accepted=False，绝不发 HTTP
  - legacy retain() 委托 retain_document 且仍返回 int（doc §26 契约不变）
  - async_retain 透传 receipt.async_processing
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from memory.hindsight_client import HindsightClient


class _CapturingClient(HindsightClient):
    """替换 _ensure_bank / _post，捕获 retain body 而不发起真实 HTTP。"""

    def __init__(self) -> None:
        super().__init__()
        self.posts: list[tuple[str, dict]] = []
        self.bank_checks: list[str] = []

    async def _ensure_bank(self, bank_id: str) -> bool:
        self.bank_checks.append(bank_id)
        return True

    async def _post(self, path, body, timeout=None, retry_on_5xx=True):
        self.posts.append((path, body))
        return {"items_count": 1, "operation_id": "op-1"}


def test_retain_document_sends_explicit_document_id():
    client = _CapturingClient()
    receipt = asyncio.run(
        client.retain_document(
            [{"role": "user", "content": "hello"}],
            "u1",
            "web-run:123",
        )
    )
    assert receipt.accepted is True
    assert receipt.items_count == 1
    assert receipt.operation_id == "op-1"
    assert client.bank_checks == ["hpagent-u-u1"]
    path, body = client.posts[0]
    assert path == "/v1/default/banks/hpagent-u-u1/memories"
    assert len(body["items"]) == 1
    assert body["items"][0]["document_id"] == "web-run:123"
    assert body["async"] is False


def test_repeated_retain_keeps_the_same_document_id():
    client = _CapturingClient()
    for content in ("a", "b"):
        asyncio.run(
            client.retain_document(
                [{"role": "user", "content": content}],
                "u1",
                "qq-execution:turn-1",
            )
        )
    asyncio.run(
        client.retain_document(
            [{"role": "user", "content": "c"}],
            "u1",
            "qq-execution:turn-2",
        )
    )
    assert [body["items"][0]["document_id"] for _, body in client.posts] == [
        "qq-execution:turn-1",
        "qq-execution:turn-1",
        "qq-execution:turn-2",
    ]


def test_events_merge_into_single_document_item():
    client = _CapturingClient()
    asyncio.run(
        client.retain_document(
            [
                {"role": "user", "content": "question"},
                {"role": "assistant", "content": "answer"},
            ],
            "u1",
            "web-run:1",
            scope="private",
            channel_type="web",
        )
    )
    _, body = client.posts[0]
    (item,) = body["items"]
    assert item["content"] == "[user]: question\n\n[assistant]: answer"
    assert "session:" not in [t for t in item["tags"]]
    assert "channel:web" in item["tags"]
    assert "scope:private" in item["tags"]


def test_empty_content_is_rejected_without_post():
    client = _CapturingClient()
    receipt = asyncio.run(
        client.retain_document([{"role": "user", "content": ""}], "u1", "doc-1")
    )
    assert receipt.accepted is False
    assert client.posts == []


def test_empty_document_id_is_rejected_without_post():
    client = _CapturingClient()
    receipt = asyncio.run(
        client.retain_document([{"role": "user", "content": "hi"}], "u1", "")
    )
    assert receipt.accepted is False
    assert client.posts == []


def test_async_retain_is_surfaced_on_the_receipt():
    client = _CapturingClient()
    receipt = asyncio.run(
        client.retain_document(
            [{"role": "user", "content": "hi"}],
            "u1",
            "web-run:2",
            async_retain=True,
        )
    )
    assert receipt.async_processing is True
    assert client.posts[0][1]["async"] is True


def test_legacy_retain_delegates_and_still_returns_int():
    client = _CapturingClient()
    count = asyncio.run(client.retain([{"role": "user", "content": "hi"}], "u1", "s1"))
    assert isinstance(count, int)
    assert count == 1
    _, body = client.posts[0]
    assert body["items"][0]["document_id"] == "session:s1"
    assert body["async"] is True
    assert body["items"][0]["metadata"] == {"session_id": "s1", "sender_name": ""}


def test_legacy_retain_disabled_mode_returns_zero():
    client = HindsightClient(enabled=False)
    assert asyncio.run(client.retain([{"role": "user", "content": "hi"}], "u1", "s1")) == 0
