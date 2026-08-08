from __future__ import annotations

import pytest

from agent_execution.facade import ExecutionRequest, ExecutionResult
from agent_execution.qq_host import (
    QQLegacyRequestLoader,
    ReplyServiceQQEventSinkFactory,
    TurnMemoryQQAuditSinkFactory,
    TurnMemoryQQRetentionSink,
)
from application.reply import ReplyService
from harness.activities import (
    inject,
    inject_qq_execution_host,
    process_turn_activity,
)
from orchestration.config import AppConfig


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

    events = ReplyServiceQQEventSinkFactory(reply).for_execution(
        "execution", message
    )
    await events.tool_progress(("search",))
    await reply.send_final("answer", message)
    await events.close()
    await events.tool_progress(("late",))

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


@pytest.mark.asyncio
async def test_qq_retain_uses_per_execution_document_after_reply():
    """Phase F：QQ retain 只取用户消息 + 最终答案，用稳定 qq-execution:{id}。

    不再使用 facade 的 memory_observations，也不再反复覆盖同一 session document。
    """
    retained = []

    class Memory:
        async def retain_document(self, **kwargs):
            retained.append(kwargs)
            return 1

    request = ExecutionRequest(
        "execution", "account", "", "session", "question", ()
    )
    await TurnMemoryQQRetentionSink(Memory()).retain(
        request,
        ExecutionResult("answer", 1, ()),
        _group_message(),
    )

    assert retained[0]["document_id"] == "qq-execution:execution"
    assert retained[0]["events"] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
    ]
    assert retained[0]["account_id"] == "account"
    assert retained[0]["session_id"] == "session"
    assert retained[0]["metadata"]["source"] == "qq"


@pytest.mark.asyncio
async def test_ae_030_qq_loader_is_retry_idempotent_and_sanitizes_metadata():
    recorded = []
    events = []

    class Event:
        def __init__(self, metadata):
            self.metadata = metadata

    class Memory:
        async def ensure_session(self, session_id, account_id, channel_type):
            return None

        async def load_recent_events(self, session_id, limit):
            return list(events)

        async def record_user_message(self, **kwargs):
            recorded.append(kwargs)
            event = Event(kwargs["metadata"])
            events.append(event)
            return event

        async def recall_memories(self, **kwargs):
            return (), "memory"

    class Context:
        def build(self, **kwargs):
            return [{"role": "user", "content": "isolated"}]

    message = _group_message()
    message["metadata"]["access_token"] = "must-not-cross-port"
    loader = QQLegacyRequestLoader(Memory(), Context())
    first = await loader.load(message, "stable-execution")
    second = await loader.load(message, "stable-execution")

    assert len(recorded) == 1
    assert first.execution_id == second.execution_id == "stable-execution"
    assert first.context == second.context
    assert first.metadata is not None
    assert "access_token" not in first.metadata
    assert first.metadata["execution_id"] == "stable-execution"
    memories = await first.context_provider.recall_long_term("rewritten")
    assert memories == ("memory",)


@pytest.mark.asyncio
async def test_qq_audit_adapter_keeps_model_and_tool_events_in_legacy_memory():
    calls = []

    class Memory:
        async def record_model_message(self, **kwargs):
            calls.append(("model", kwargs))

        async def record_tool_result(self, **kwargs):
            calls.append(("tool", kwargs))

    class Result:
        display_result = "summary"
        error = None
        output = "raw"
        metadata = {"safe": True}

    request = ExecutionRequest(
        "execution", "account", "", "session", "question", ()
    )
    audit = TurnMemoryQQAuditSinkFactory(Memory()).for_execution(
        "execution", request
    )
    await audit.model_step(
        "execution", 1, "thinking", ({"name": "search"},), "tool_use", None
    )
    await audit.tool_result("execution", "call", "search", Result())

    assert [item[0] for item in calls] == ["model", "tool"]
    assert calls[0][1]["session_id"] == "session"
    assert calls[1][1]["result"] == "summary"


@pytest.mark.asyncio
async def test_d_05_feature_flag_selects_once_and_never_falls_back_after_failure():
    calls = []

    class Legacy:
        async def process_turn(self, user_message):
            calls.append("legacy")
            return {"content": "legacy"}

    class NewHost:
        async def execute(self, workflow_id, user_message):
            calls.append(("new", workflow_id))
            raise RuntimeError("new path failed after selection")

    message = _group_message()
    inject(Legacy())
    try:
        inject_qq_execution_host(NewHost(), enabled=False)
        assert (await process_turn_activity(message))["content"] == "legacy"

        inject_qq_execution_host(NewHost(), enabled=True)
        with pytest.raises(RuntimeError, match="new path failed"):
            await process_turn_activity(message)
    finally:
        inject_qq_execution_host(None, enabled=False)

    assert calls == ["legacy", ("new", "direct-session")]


def test_d_05_new_qq_host_feature_flag_defaults_off_and_has_env_override():
    config = AppConfig()
    assert config.agent.qq_execution_host_enabled is False
    config._apply_env_overrides({"QQ_EXECUTION_HOST_ENABLED": "true"})
    assert config.agent.qq_execution_host_enabled is True
