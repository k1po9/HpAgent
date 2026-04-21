import pytest
import asyncio
from src.new_arch.harness.context_builder import HarnessContextBuilder
from src.new_arch.common.types import Event, EventType


@pytest.fixture
def context_builder():
    return HarnessContextBuilder(system_prompt="You are a helpful assistant.")


def test_build_empty_context(context_builder):
    messages = context_builder.build([])
    assert len(messages) == 1
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You are a helpful assistant."


def test_build_with_user_message(context_builder):
    events = [
        Event(
            event_id="1",
            session_id="test",
            event_type=EventType.USER_MESSAGE,
            content={"text": "Hello!"},
        )
    ]

    messages = context_builder.build(events)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Hello!"


def test_build_with_model_message(context_builder):
    events = [
        Event(
            event_id="1",
            session_id="test",
            event_type=EventType.MODEL_MESSAGE,
            content={"text": "Hi there!", "tool_calls": []},
        )
    ]

    messages = context_builder.build(events)

    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert messages[1]["content"] == "Hi there!"


def test_build_with_tool_calls(context_builder):
    events = [
        Event(
            event_id="1",
            session_id="test",
            event_type=EventType.MODEL_MESSAGE,
            content={
                "text": "",
                "tool_calls": [
                    {"id": "call_1", "name": "calculator", "arguments": {"expression": "2+2"}}
                ],
            },
        )
    ]

    messages = context_builder.build(events)

    assert len(messages) == 2
    assert messages[1]["role"] == "assistant"
    assert len(messages[1]["content"]) == 1
    assert messages[1]["content"][0]["type"] == "tool_use"


def test_build_conversation_flow(context_builder):
    events = [
        Event(
            event_id="1",
            session_id="test",
            event_type=EventType.USER_MESSAGE,
            content={"text": "What's 2+2?"},
        ),
        Event(
            event_id="2",
            session_id="test",
            event_type=EventType.MODEL_MESSAGE,
            content={
                "text": "",
                "tool_calls": [
                    {"id": "call_1", "name": "calculator", "arguments": {"expression": "2+2"}}
                ],
            },
        ),
        Event(
            event_id="3",
            session_id="test",
            event_type=EventType.TOOL_RESULT,
            content={"tool_call_id": "call_1", "result": "4", "error": None},
        ),
        Event(
            event_id="4",
            session_id="test",
            event_type=EventType.MODEL_MESSAGE,
            content={"text": "2+2 equals 4!", "tool_calls": []},
        ),
    ]

    messages = context_builder.build(events)

    assert len(messages) == 5
    assert messages[1]["role"] == "user"
    assert messages[2]["role"] == "assistant"
    assert messages[3]["role"] == "user"
    assert "4" in messages[3]["content"]
    assert messages[4]["role"] == "assistant"


def test_max_turns_limit(context_builder):
    events = []
    for i in range(50):
        events.append(
            Event(
                event_id=str(i),
                session_id="test",
                event_type=EventType.USER_MESSAGE,
                content={"text": f"Message {i}"},
            )
        )

    messages = context_builder.build(events, max_turns=10)

    assert len(messages) <= 22
