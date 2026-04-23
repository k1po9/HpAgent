"""
Session 层持久化仓库实现
采用文件存储（JSON），支持内存缓存与线程安全
"""
import json
from pathlib import Path
from typing import Dict, List, Optional
from threading import RLock

from .models import Session, EventRecord


class FileSessionRepository:
    """会话元数据仓库"""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._sessions: Dict[str, Session] = {}
        self._lock = RLock()
        if storage_path:
            self._load()

    def _load(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path) / "sessions.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for item in data:
                    session = Session.from_dict(item)
                    self._sessions[session.session_id] = session
        except Exception:
            pass

    def _save(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        sessions_file = path / "sessions.json"
        with open(sessions_file, "w", encoding="utf-8") as f:
            json.dump(
                [s.to_dict() for s in self._sessions.values()],
                f,
                ensure_ascii=False,
                indent=2,
            )

    def save(self, session: Session) -> None:
        with self._lock:
            self._sessions[session.session_id] = session
            self._save()

    def get(self, session_id: str) -> Optional[Session]:
        with self._lock:
            return self._sessions.get(session_id)

    def list_all(self) -> List[Session]:
        with self._lock:
            return list(self._sessions.values())


class FileEventRepository:
    """事件日志仓库"""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = storage_path
        self._events: Dict[str, List[EventRecord]] = {}
        self._counters: Dict[str, int] = {}
        self._lock = RLock()
        if storage_path:
            self._load()

    def _load(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path) / "events.json"
        if not path.exists():
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                for session_id, events_list in data.items():
                    records = [EventRecord.from_dict(e) for e in events_list]
                    self._events[session_id] = records
                    if records:
                        max_index = max(e.event_index for e in records)
                        self._counters[session_id] = max_index + 1
        except Exception:
            pass

    def _save(self) -> None:
        if not self._storage_path:
            return
        path = Path(self._storage_path)
        path.mkdir(parents=True, exist_ok=True)
        events_file = path / "events.json"
        with open(events_file, "w", encoding="utf-8") as f:
            json.dump(
                {
                    sid: [e.to_dict() for e in events]
                    for sid, events in self._events.items()
                },
                f,
                ensure_ascii=False,
                indent=2,
            )

    def append_event(self, session_id: str, event: EventRecord) -> int:
        with self._lock:
            if session_id not in self._events:
                self._events[session_id] = []
                self._counters[session_id] = 0
            index = self._counters[session_id]
            event.event_index = index
            self._events[session_id].append(event)
            self._counters[session_id] = index + 1
            self._save()
            return index

    def get_events(
        self,
        session_id: str,
        offset: int = 0,
        limit: Optional[int] = None,
        event_types: Optional[List[str]] = None,
    ) -> List[EventRecord]:
        with self._lock:
            events = self._events.get(session_id, [])
            if event_types:
                events = [e for e in events if e.event_type in event_types]
            events = events[offset:]
            if limit is not None:
                events = events[:limit]
            return events

    def truncate_events(self, session_id: str, target_index: int) -> int:
        with self._lock:
            events = self._events.get(session_id, [])
            original_len = len(events)
            self._events[session_id] = events[:target_index]
            self._counters[session_id] = target_index
            self._save()
            return original_len - target_index

    def get_event_count(self, session_id: str) -> int:
        with self._lock:
            return self._counters.get(session_id, 0)