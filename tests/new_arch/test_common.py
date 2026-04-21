import pytest
from src.new_arch.common.types import (
    Event, EventType, ToolCall, ToolResult, ModelResponse,
    UnifiedMessage, ChannelType, StopReason, SessionMetadata
)


def test_event_creation():
    event = Event(
        session_id="test_session",
        event_type=EventType.USER_MESSAGE,
        content={"text": "Hello"},
    )

    assert event.session_id == "test_session"
    assert event.event_type == EventType.USER_MESSAGE
    assert event.content["text"] == "Hello"
    assert event.event_id is not None
    assert event.timestamp > 0


def test_event_to_dict():
    event = Event(
        session_id="test_session",
        event_type=EventType.MODEL_MESSAGE,
        content={"text": "Hi there!"},
    )

    event_dict = event.to_dict()

    assert event_dict["session_id"] == "test_session"
    assert event_dict["event_type"] == "model_message"
    assert event_dict["content"]["text"] == "Hi there!"


def test_event_from_dict():
    data = {
        "event_id": "evt_123",
        "session_id": "session_456",
        "timestamp": 1234567890.0,
        "event_type": "tool_call",
        "content": {"tool_name": "test"},
        "metadata": {},
    }

    event = Event.from_dict(data)

    assert event.event_id == "evt_123"
    assert event.session_id == "session_456"
    assert event.event_type == EventType.TOOL_CALL


def test_tool_call():
    tool_call = ToolCall(
        id="call_1",
        name="calculator",
        arguments={"expression": "2+2"},
    )

    assert tool_call.id == "call_1"
    assert tool_call.name == "calculator"
    assert tool_call.arguments["expression"] == "2+2"

    tool_call_dict = tool_call.to_dict()
    assert tool_call_dict["id"] == "call_1"

    restored = ToolCall.from_dict(tool_call_dict)
    assert restored.id == tool_call.id
    assert restored.name == tool_call.name


def test_tool_result():
    result = ToolResult(
        tool_call_id="call_1",
        status="success",
        content="4",
    )

    assert result.status == "success"
    assert result.error is None

    result_dict = result.to_dict()
    assert result_dict["status"] == "success"
    assert result_dict["content"] == "4"


def test_tool_result_error():
    result = ToolResult(
        tool_call_id="call_1",
        status="error",
        error="Division by zero",
    )

    assert result.status == "error"
    assert result.error == "Division by zero"


def test_model_response():
    response = ModelResponse(
        content="Hello!",
        tool_calls=None,
        stop_reason=StopReason.END_TURN,
    )

    assert response.content == "Hello!"
    assert response.tool_calls is None
    assert response.stop_reason == StopReason.END_TURN


def test_model_response_with_tools():
    tool_calls = [
        ToolCall(id="call_1", name="search", arguments={"query": "test"}),
    ]

    response = ModelResponse(
        content="",
        tool_calls=tool_calls,
        stop_reason=StopReason.TOOL_USE,
    )

    assert len(response.tool_calls) == 1
    assert response.tool_calls[0].name == "search"


def test_unified_message():
    msg = UnifiedMessage(
        session_id="session_123",
        sender_id="user_456",
        channel_type=ChannelType.DISCORD,
        content="Hello from Discord!",
    )

    assert msg.session_id == "session_123"
    assert msg.channel_type == ChannelType.DISCORD

    event = msg.to_event()
    assert event.event_type == EventType.USER_MESSAGE
    assert event.content["content"] == "Hello from Discord!"
    assert event.content["channel_type"] == "discord"


def test_session_metadata():
    metadata = SessionMetadata(
        session_id="session_123",
        creator_id="user_456",
        channel_type=ChannelType.CONSOLE,
        tags=["test", "important"],
    )

    assert metadata.session_id == "session_123"
    assert "test" in metadata.tags

    data = metadata.to_dict()
    assert data["channel_type"] == "console"
