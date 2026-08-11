from __future__ import annotations

import pytest

from agent_execution.brain_action_loop import DefaultBrainActionLoop
from application.memory_reflection import MemoryReflectionService
from application.session_archive import SessionArchiveService


def test_shared_loop_strips_leaked_xml_tool_calls_from_final_reply():
    assert DefaultBrainActionLoop._safe_final_content(
        "before <tool_call>{}</tool_call> after"
    ) == "before  after"
    assert DefaultBrainActionLoop._safe_final_content(
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


@pytest.mark.asyncio
async def test_session_archive_preserves_write_cleanup_summary_order(monkeypatch):
    calls = []

    class Memory:
        async def archive_events(self, session_id):
            calls.append("archive_events")
            return [
                {
                    "event_type": "model_message",
                    "content": {"tool_calls": [{"name": "search"}]},
                }
            ]

        async def delete_wal(self, session_id):
            calls.append("delete_wal")

    class Actions:
        def clear_session(self, session_id):
            calls.append("clear_session")

    def write_history(*args):
        calls.append("write_history")

    async def summarize(*args):
        calls.append("summarize")
        return "summary", ["tag"]

    def update_meta(*args, **kwargs):
        calls.append("update_meta")

    monkeypatch.setattr("session.workspace.write_history_jsonl", write_history)
    monkeypatch.setattr("session.workspace.generate_session_summary", summarize)
    monkeypatch.setattr("session.workspace.update_session_meta", update_meta)

    service = SessionArchiveService(
        memory_service=Memory(),
        action_runtime=Actions(),
        resource_pool=object(),
        prompts=object(),
        file_store=object(),
    )
    result = await service.archive("session", "account")

    assert calls == [
        "archive_events",
        "write_history",
        "clear_session",
        "delete_wal",
        "summarize",
        "update_meta",
    ]
    assert result == {
        "ok": True,
        "task_summary": "summary",
        "tags": ["tag"],
        "event_count": 1,
        "tool_calls": 1,
        "tools_used": ["search"],
    }
