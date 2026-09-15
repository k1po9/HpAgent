import inspect
from unittest.mock import AsyncMock, Mock

import pytest
from support.qq_messages import qq_message

from application.conversation import InvalidQQMessage, normalize_qq_message
from application.ingress import MessageIngressService
from channels.napcat import NapCatChannel
from channels.official_qq import OfficialQQChannel


@pytest.mark.asyncio
async def test_napcat_raw_id_and_bot_are_preserved_across_normalization():
    channel = NapCatChannel()
    raw = {"post_type": "message", "message_type": "private", "self_id": 999,
           "message_id": -123, "sender": {"user_id": 123}, "message": "hi"}
    first, second = await channel.normalize_message(raw), await channel.normalize_message(raw)
    assert first.message_id == "-123" and first.metadata["self_id"] == "999"
    assert normalize_qq_message(first, "napcat").message_key == normalize_qq_message(second, "napcat").message_key


@pytest.mark.asyncio
@pytest.mark.parametrize("event,scope,extra", [
    ("C2C_MESSAGE_CREATE", "private", {}),
    ("GROUP_AT_MESSAGE_CREATE", "group", {"group_openid": "group"}),
    ("AT_MESSAGE_CREATE", "guild", {"guild_id": "guild", "channel_id": "channel", "thread_id": "t"}),
    ("DIRECT_MESSAGE_CREATE", "dm", {"guild_id": "dm-guild"}),
])
async def test_official_qq_routing_and_stable_message_id(event, scope, extra):
    channel = OfficialQQChannel()
    channel._app_id = "app-bot"
    raw = {"t": event, "d": {"id": "not-a-uuid:provider-message", "content": "hi", "author": {"id": "openid"}, **extra}}
    message = await channel.normalize_message(raw)
    source = normalize_qq_message(message, "official_qq")
    assert source.triggered and source.route["scope"] == scope
    assert source.route["bot_id"] == "app-bot" and source.subject == "official_qq:openid"
    assert source.origin["external_message_id"] == message.message_id
    assert len(source.message_key) <= 128


@pytest.mark.parametrize("field", ["self_id", "message_id"])
def test_missing_provider_identity_does_not_use_generated_uuid(field):
    message = qq_message()
    del message.metadata[field]
    with pytest.raises(InvalidQQMessage):
        normalize_qq_message(message, "napcat")


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_available", [False, True])
async def test_non_trigger_group_never_calls_commands_even_without_redis(cache_available):
    commands = Mock(accept=AsyncMock())
    cache = Mock(append=AsyncMock(side_effect=RuntimeError("Redis down"))) if cache_available else None
    ingress = MessageIngressService(group_context=cache, conversation_service=commands)
    await ingress.handle(qq_message(scope="group", trigger=False))
    commands.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_trigger_group_survives_redis_failure():
    commands = Mock(accept=AsyncMock(return_value=None))
    ingress = MessageIngressService(
        group_context=Mock(append=AsyncMock(side_effect=RuntimeError("Redis down"))),
        conversation_service=commands,
    )
    await ingress.handle(qq_message(scope="group"))
    commands.accept.assert_awaited_once()
    assert commands.accept.call_args.kwargs["group_context"] == ""


def test_production_composition_has_no_qq_execution_queue():
    from bootstrap import qq
    from orchestration import worker
    source = inspect.getsource(worker.start_worker)
    assert "OrchestrationWorkflow" not in source and "process_turn_activity" not in source
    assert "SurfaceConversationCommands" in source
    assert "QQExecutionHost" not in inspect.getsource(qq.build_qq_runtime)
    assert "SessionStore(" not in inspect.getsource(qq.build_qq_runtime)
    assert "DefaultBrainActionLoop" not in inspect.getsource(qq.build_qq_runtime)
