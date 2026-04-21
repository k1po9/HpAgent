from typing import Optional, Dict, Any, List
from .event_store import EventStore
from .models import Session
from ..common.types import Event, SessionMetadata, ChannelType, EventType
from ..common.interfaces import ISession
from ..common.errors import SessionNotFoundError
import uuid
import time


class SessionManager:
    def __init__(self, event_store: EventStore):
        self._event_store = event_store

    async def create_session(self, creator_id: str = "", channel_type: ChannelType = ChannelType.CONSOLE, tags: Optional[list] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        session_id = str(uuid.uuid4())
        session_metadata = SessionMetadata(
            session_id=session_id,
            creator_id=creator_id,
            channel_type=channel_type,
            tags=tags or [],
            created_at=time.time(),
            status="active",
        )
        session_metadata.metadata = metadata or {}
        await self._event_store.create_session(session_metadata)
        start_event = Event(
            session_id=session_id,
            event_type=EventType.SESSION_START,
            content={"creator_id": creator_id, "channel_type": channel_type.value if hasattr(channel_type, 'value') else str(channel_type)},
            metadata={"initial": True},
        )
        await self._event_store.emit_event(start_event)
        return session_id

    async def append_user_message(self, session_id: str, content: str, sender_id: str = "", metadata: Optional[Dict[str, Any]] = None) -> str:
        event = Event(session_id=session_id, event_type=EventType.USER_MESSAGE, content={"text": content, "sender_id": sender_id}, metadata=metadata or {})
        return await self._event_store.emit_event(event)

    async def append_model_message(self, session_id: str, content: str, tool_calls: Optional[list] = None, metadata: Optional[Dict[str, Any]] = None) -> str:
        event = Event(session_id=session_id, event_type=EventType.MODEL_MESSAGE, content={"text": content, "tool_calls": tool_calls or []}, metadata=metadata or {})
        return await self._event_store.emit_event(event)

    async def append_tool_call(self, session_id: str, tool_name: str, arguments: Dict[str, Any], tool_call_id: str = "") -> str:
        event = Event(session_id=session_id, event_type=EventType.TOOL_CALL, content={"tool_name": tool_name, "arguments": arguments, "tool_call_id": tool_call_id or str(uuid.uuid4())})
        return await self._event_store.emit_event(event)

    async def append_tool_result(self, session_id: str, tool_call_id: str, result: Any, error: Optional[str] = None) -> str:
        event = Event(session_id=session_id, event_type=EventType.TOOL_RESULT, content={"tool_call_id": tool_call_id, "result": result, "error": error, "status": "error" if error else "success"})
        return await self._event_store.emit_event(event)

    async def append_error(self, session_id: str, error_type: str, message: str, details: Optional[Dict[str, Any]] = None) -> str:
        event = Event(session_id=session_id, event_type=EventType.ERROR, content={"error_type": error_type, "message": message, "details": details or {}})
        return await self._event_store.emit_event(event)

    async def complete_session(self, session_id: str) -> str:
        event = Event(session_id=session_id, event_type=EventType.SESSION_COMPLETE, content={})
        event_id = await self._event_store.emit_event(event)
        await self._event_store.archive_session(session_id)
        return event_id

    async def get_full_history(self, session_id: str) -> list[Event]:
        return await self._event_store.get_events(session_id)

    async def rewind_to_event(self, session_id: str, event_id: str) -> Dict[str, Any]:
        return await self._event_store.rewind_session(session_id, event_id)

    def get_session_info(self, session_id: str) -> Optional[Session]:
        return self._event_store.get_session(session_id)

    async def list_active_sessions(self, limit: int = 50, offset: int = 0) -> list[SessionMetadata]:
        return await self._event_store.list_sessions(limit=limit, offset=offset, status="active")
