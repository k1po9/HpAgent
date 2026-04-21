from typing import Dict, List, Optional, Any
from threading import RLock
from enum import Enum
import json
from pathlib import Path
from ..common.types import Event, EventType, SessionMetadata, ChannelType
from .models import Session, EventRecord, SessionStatus
from ..common.interfaces import ISession
from ..common.errors import SessionNotFoundError, ValidationError


class EventStore(ISession):
    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._sessions: Dict[str, Session] = {}
        self._events: Dict[str, List[EventRecord]] = {}
        self._event_counters: Dict[str, int] = {}
        self._lock = RLock()
        if storage_path:
            self._load_from_disk()

    def _load_from_disk(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        if not path.exists():
            return
        try:
            sessions_file = path / "sessions.json"
            if sessions_file.exists():
                with open(sessions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for session_data in data:
                        session = Session.from_dict(session_data)
                        self._sessions[session.session_id] = session
            events_file = path / "events.json"
            if events_file.exists():
                with open(events_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for session_id, events_list in data.items():
                        self._events[session_id] = [EventRecord.from_dict(e) for e in events_list]
                        if events_list:
                            max_index = max(e["event_index"] for e in events_list)
                            self._event_counters[session_id] = max_index + 1
        except Exception:
            pass

    def _save_to_disk(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        sessions_file = path / "sessions.json"
        with open(sessions_file, "w", encoding="utf-8") as f:
            json.dump([s.to_dict() for s in self._sessions.values()], f, ensure_ascii=False, indent=2)
        events_file = path / "events.json"
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump({sid: [e.to_dict() for e in events] for sid, events in self._events.items()}, f, ensure_ascii=False, indent=2)

    async def create_session(self, metadata: SessionMetadata) -> str:
        with self._lock:
            if metadata.session_id in self._sessions:
                raise ValidationError("session_id", "Session already exists")
            session = Session(
                session_id=metadata.session_id,
                creator_id=metadata.creator_id,
                channel_type=metadata.channel_type.value if hasattr(metadata.channel_type, 'value') else str(metadata.channel_type),
                tags=metadata.tags,
                status=SessionStatus.ACTIVE,
                created_at=metadata.created_at,
            )
            self._sessions[session.session_id] = session
            self._events[session.session_id] = []
            self._event_counters[session.session_id] = 0
            self._save_to_disk()
            return session.session_id

    async def emit_event(self, event: Event) -> str:
        with self._lock:
            if event.session_id not in self._sessions:
                raise SessionNotFoundError(event.session_id)
            session = self._sessions[event.session_id]
            if session.status != SessionStatus.ACTIVE:
                raise ValidationError("session_status", f"Cannot emit event to {session.status.value} session")
            event_index = self._event_counters[event.session_id]
            record = EventRecord(
                event_id=event.event_id,
                session_id=event.session_id,
                event_index=event_index,
                timestamp=event.timestamp,
                event_type=event.event_type.value if isinstance(event.event_type, Enum) else event.event_type,
                content=event.content,
                metadata=event.metadata,
            )
            self._events[event.session_id].append(record)
            self._event_counters[event.session_id] += 1
            session.updated_at = event.timestamp
            self._save_to_disk()
            return event.event_id

    async def get_events(self, session_id: str, offset: int = 0, limit: Optional[int] = None, event_types: Optional[List[str]] = None) -> List[Event]:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            events = self._events.get(session_id, [])
            if event_types:
                events = [e for e in events if e.event_type in event_types]
            events = events[offset:]
            if limit is not None:
                events = events[:limit]
            return [Event(event_id=e.event_id, session_id=e.session_id, timestamp=e.timestamp, event_type=EventType(e.event_type), content=e.content, metadata=e.metadata) for e in events]

    async def rewind_session(self, session_id: str, target_event_id: str) -> Dict[str, Any]:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            events = self._events.get(session_id, [])
            target_index = None
            for i, event in enumerate(events):
                if event.event_id == target_event_id:
                    target_index = i
                    break
            if target_index is None:
                raise ValidationError("target_event_id", "Event not found")
            removed_count = len(events) - target_index
            self._events[session_id] = events[:target_index + 1]
            session = self._sessions[session_id]
            session.updated_at = events[target_index].timestamp
            self._save_to_disk()
            return {"session_id": session_id, "rewound_to_event_id": target_event_id, "removed_events_count": removed_count}

    async def archive_session(self, session_id: str) -> bool:
        with self._lock:
            if session_id not in self._sessions:
                raise SessionNotFoundError(session_id)
            session = self._sessions[session_id]
            session.status = SessionStatus.ARCHIVED
            self._save_to_disk()
            return True

    async def list_sessions(self, limit: int = 50, offset: int = 0, status: Optional[str] = None, tags: Optional[List[str]] = None) -> List[SessionMetadata]:
        with self._lock:
            sessions = list(self._sessions.values())
            if status:
                sessions = [s for s in sessions if s.status.value == status]
            if tags:
                sessions = [s for s in sessions if any(tag in s.tags for tag in tags)]
            sessions = sorted(sessions, key=lambda s: s.created_at, reverse=True)
            sessions = sessions[offset:offset + limit]
            return [SessionMetadata(session_id=s.session_id, creator_id=s.creator_id, channel_type=ChannelType(s.channel_type) if s.channel_type else ChannelType.CONSOLE, tags=s.tags, created_at=s.created_at, status=s.status.value if isinstance(s.status, Enum) else s.status) for s in sessions]

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def get_event_count(self, session_id: str) -> int:
        return self._event_counters.get(session_id, 0)
