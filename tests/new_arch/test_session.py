import pytest
import asyncio
from src.new_arch.session.event_store import EventStore
from src.new_arch.session.session_manager import SessionManager
from src.new_arch.common.types import Event, EventType, SessionMetadata, ChannelType
from src.new_arch.common.errors import SessionNotFoundError


@pytest.fixture
def event_store():
    return EventStore()


@pytest.fixture
def session_manager(event_store):
    return SessionManager(event_store)


@pytest.mark.asyncio
async def test_create_session(session_manager):
    session_id = await session_manager.create_session(
        creator_id="test_user",
        channel_type=ChannelType.CONSOLE,
        tags=["test"],
    )

    assert session_id is not None
    assert len(session_id) > 0

    session_info = session_manager.get_session_info(session_id)
    assert session_info is not None
    assert session_info.creator_id == "test_user"


@pytest.mark.asyncio
async def test_append_user_message(session_manager):
    session_id = await session_manager.create_session()

    event_id = await session_manager.append_user_message(
        session_id=session_id,
        content="Hello, world!",
        sender_id="test_user",
    )

    assert event_id is not None

    history = await session_manager.get_full_history(session_id)
    assert len(history) == 2
    assert history[1].event_type == EventType.USER_MESSAGE
    assert history[1].content["text"] == "Hello, world!"


@pytest.mark.asyncio
async def test_append_model_message(session_manager):
    session_id = await session_manager.create_session()

    await session_manager.append_model_message(
        session_id=session_id,
        content="Hello! How can I help you?",
        tool_calls=[
            {"id": "call_1", "name": "calculator", "arguments": {"expression": "2+2"}}
        ],
    )

    history = await session_manager.get_full_history(session_id)
    model_events = [e for e in history if e.event_type == EventType.MODEL_MESSAGE]
    assert len(model_events) == 1
    assert model_events[0].content["text"] == "Hello! How can I help you?"
    assert len(model_events[0].content["tool_calls"]) == 1


@pytest.mark.asyncio
async def test_session_not_found(event_store):
    with pytest.raises(SessionNotFoundError):
        await event_store.get_events("nonexistent_session")


@pytest.mark.asyncio
async def test_rewind_session(session_manager):
    session_id = await session_manager.create_session()

    await session_manager.append_user_message(session_id, "First message")
    await session_manager.append_model_message(session_id, "First response")
    event_to_rewind = await session_manager.append_user_message(session_id, "Second message")

    history_before = await session_manager.get_full_history(session_id)
    assert len(history_before) == 4

    await session_manager.rewind_to_event(session_id, event_to_rewind)

    history_after = await session_manager.get_full_history(session_id)
    assert len(history_after) == 4


@pytest.mark.asyncio
async def test_list_active_sessions(session_manager):
    await session_manager.create_session(creator_id="user1")
    await session_manager.create_session(creator_id="user2")

    sessions = await session_manager.list_active_sessions(limit=10)
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_complete_session(session_manager):
    session_id = await session_manager.create_session()

    await session_manager.append_user_message(session_id, "Hello")
    await session_manager.complete_session(session_id)

    session_info = session_manager.get_session_info(session_id)
    assert session_info.status.value == "archived"
