"""
TurnMemoryQQRetentionSink —— Phase F QQ per-execution Hindsight document。

⑨ doc §28-31：QQ 每轮成功交互只 retain 用户实际说的话 + 最终实际收到的答案，
使用稳定幂等的 ``qq-execution:{execution_id}`` document_id，不再用本轮内容反复
覆盖同一个 session document。
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_execution.facade import ExecutionRequest, ExecutionResult
from agent_execution.qq_host import TurnMemoryQQRetentionSink


class _FakeMemory:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def retain_document(self, **kwargs):
        self.calls.append(kwargs)
        return 1


def _request(execution_id: str = "qq-turn-abc") -> ExecutionRequest:
    return ExecutionRequest(
        execution_id, "acc-1", "", "sess-1", "question", ()
    )


def _user_message(**overrides) -> dict:
    message = {
        "message_id": "m1",
        "channel_type": "napcat",
        "metadata": {"detail_type": "private", "sender_name": "Alice"},
    }
    message.update(overrides)
    return message


@pytest.mark.asyncio
async def test_qq_retention_uses_per_execution_document_id():
    memory = _FakeMemory()
    await TurnMemoryQQRetentionSink(memory).retain(
        _request(),
        ExecutionResult("answer", 1, ()),
        _user_message(),
    )
    assert len(memory.calls) == 1
    call = memory.calls[0]
    assert call["document_id"] == "qq-execution:qq-turn-abc"
    assert call["events"] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    assert call["account_id"] == "acc-1"
    assert call["channel_type"] == "napcat"
    assert call["session_id"] == "sess-1"
    assert call["metadata"]["source"] == "qq"
    assert call["metadata"]["execution_id"] == "qq-turn-abc"
    assert call["metadata"]["session_id"] == "sess-1"
    assert call["metadata"]["sender_name"] == "Alice"


@pytest.mark.asyncio
async def test_qq_retention_does_not_reuse_session_document():
    memory = _FakeMemory()
    sink = TurnMemoryQQRetentionSink(memory)
    await sink.retain(
        _request("qq-turn-1"),
        ExecutionResult("answer one", 1, ()),
        _user_message(),
    )
    await sink.retain(
        _request("qq-turn-2"),
        ExecutionResult("answer two", 1, ()),
        _user_message(),
    )
    document_ids = [call["document_id"] for call in memory.calls]
    assert document_ids == ["qq-execution:qq-turn-1", "qq-execution:qq-turn-2"]
    # 两次独立 execution 用两个不同的 document，绝不复用/覆盖同一个。
    assert len(set(document_ids)) == 2
