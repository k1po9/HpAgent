from typing import Dict, List, Any, Optional
from ..common.types import Event, EventType, ChannelType
from ..session.session_manager import SessionManager
from ..session.models import Session, SessionStatus
import time
import uuid


class LegacySessionConverter:
    def __init__(self, session_manager: SessionManager):
        self._session_manager = session_manager

    def convert_session(self, legacy_session: Dict[str, Any], target_session_id: Optional[str] = None) -> str:
        session_id = target_session_id or legacy_session.get("session_key", str(uuid.uuid4()))
        creator_id = legacy_session.get("creator_id", "")
        channel_type_str = legacy_session.get("provider", "console")
        try:
            channel_type = ChannelType(channel_type_str)
        except ValueError:
            channel_type = ChannelType.CONSOLE
        session = Session(session_id=session_id, status=SessionStatus.ACTIVE, creator_id=creator_id, channel_type=channel_type.value, tags=legacy_session.get("tags", []), created_at=legacy_session.get("created_at", time.time()), updated_at=legacy_session.get("updated_at", time.time()))
        import asyncio
        asyncio.run(self._create_session_sync(session))
        conversation_history = legacy_session.get("conversation_history", [])
        if conversation_history:
            self._convert_conversation_history(session_id, conversation_history)
        return session_id

    async def _create_session_sync(self, session: Session) -> None:
        from ..common.types import SessionMetadata
        metadata = SessionMetadata(session_id=session.session_id, creator_id=session.creator_id, channel_type=ChannelType(session.channel_type), tags=session.tags, created_at=session.created_at, status="active")
        await self._session_manager.create_session(metadata)
        start_event = Event(session_id=session.session_id, event_type=EventType.SESSION_START, content={"creator_id": session.creator_id, "channel_type": session.channel_type}, metadata={"legacy_migration": True})
        await self._session_manager.emit_event(start_event)

    def _convert_conversation_history(self, session_id: str, conversation_history: List[Dict[str, Any]]) -> None:
        for msg in conversation_history:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                event = Event(session_id=session_id, event_type=EventType.CONFIG_CHANGE, content={"change_type": "system_prompt", "content": content}, metadata={"legacy_migration": True})
            elif role == "user":
                event = Event(session_id=session_id, event_type=EventType.USER_MESSAGE, content={"text": content, "sender_id": msg.get("sender_id", "")}, metadata={"legacy_migration": True})
            elif role == "assistant":
                tool_calls = msg.get("tool_calls", [])
                event = Event(session_id=session_id, event_type=EventType.MODEL_MESSAGE, content={"text": content, "tool_calls": tool_calls}, metadata={"legacy_migration": True})
            else:
                continue
            import asyncio
            asyncio.run(self._emit_event_sync(event))

    async def _emit_event_sync(self, event: Event) -> None:
        await self._session_manager.emit_event(event)
