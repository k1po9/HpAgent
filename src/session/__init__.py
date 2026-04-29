"""
Session — the memory layer.

Provides:
  - SessionManager:          File-backed ISession implementation (legacy).
  - TemporalSessionManager:  Temporal-backed ISession implementation (reads via Workflow Queries).
  - Session, EventRecord:    Data models.
  - FileSessionRepository, FileEventRepository: Persistence repositories.
"""
from .session_manager import SessionManager, TemporalSessionManager
from .models import Session, EventRecord, SessionStatus
from .repositories import FileSessionRepository, FileEventRepository

__all__ = [
    "SessionManager",
    "TemporalSessionManager",
    "Session",
    "EventRecord",
    "SessionStatus",
    "FileSessionRepository",
    "FileEventRepository",
]