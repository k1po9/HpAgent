import pytest
from src.common.types import Event, EventType, ToolCall, ToolResult, ModelResponse, UnifiedMessage, ChannelType, StopReason, SessionMetadata


def test_event_creation():
    event = Event(session_id="test_session", event_type=EventType.USER_MESSAGE, content={"text": "Hello"})
    assert event.session_id == "test_session"
    assert event.event_type == EventType.USER_MESSAGE
    assert event.content["text"] == "Hello"
    assert event.event_id is not None


def test_event_to_dict():
    event = Event(session_id="test_session", event_type=EventType.MODEL_MESSAGE, content={"text": "Hi there!"})
    event_dict = event.to_dict()
    assert event_dict["session_id"] == "test_session"
    assert event_dict["event_type"] == "model_message"


def test_event_from_dict():
    data = {"event_id": "evt_123", "session_id": "session_456", "timestamp": 1234567890.0, "event_type": "tool_call", "content": {"tool_name": "test"}, "metadata": {}}
    event = Event.from_dict(data)
    assert event.event_id == "evt_123"
    assert event.event_type == EventType.TOOL_CALL


def test_tool_call():
    tool_call = ToolCall(id="call_1", name="calculator", arguments={"expression": "2+2"})
    assert tool_call.id == "call_1"
    assert tool_call.name == "calculator"
    assert tool_call.arguments["expression"] == "2+2"


def test_tool_result():
    result = ToolResult(tool_call_id="call_1", status="success", content="4")
    assert result.status == "success"
    assert result.error is None


def test_model_response():
    response = ModelResponse(content="Hello!", tool_calls=None, stop_reason=StopReason.END_TURN)
    assert response.content == "Hello!"
    assert response.stop_reason == StopReason.END_TURN


def test_unified_message():
    msg = UnifiedMessage(session_id="session_123", sender_id="user_456", channel_type=ChannelType.DISCORD, content="Hello from Discord!")
    assert msg.channel_type == ChannelType.DISCORD
    event = msg.to_event()
    assert event.event_type == EventType.USER_MESSAGE


def test_session_metadata():
    metadata = SessionMetadata(session_id="session_123", creator_id="user_456", channel_type=ChannelType.CONSOLE, tags=["test", "important"])
    assert metadata.session_id == "session_123"
    assert "test" in metadata.tags
