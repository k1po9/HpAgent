from __future__ import annotations

import pytest

from agent_activities.runtime import DurableAgentActivities
from application.memory_reflection import MemoryReflectionService


def test_durable_model_activity_strips_leaked_xml_tool_calls_from_final_reply():
    assert DurableAgentActivities._safe_final_content(
        "before <tool_call>{}</tool_call> after"
    ) == "before  after"
    assert DurableAgentActivities._safe_final_content(
        "<tool_call>{}</tool_call>"
    ) == "抱歉，我暂时无法处理这个消息，请稍后重试。"


@pytest.mark.asyncio
async def test_memory_reflection_batch_isolates_account_failures():
    class Memory:
        async def reflect(self, account_id):
            if account_id == "broken":
                raise RuntimeError("unavailable")
            return 3

    result = await MemoryReflectionService(Memory()).reflect_batch(["ok", "broken"])

    assert result == {"results": {"ok": 3, "broken": -1}, "total": 2}
