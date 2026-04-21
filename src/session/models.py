from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import time


class SessionStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPLETED = "completed"


@dataclass
class Session:
    session_id: str
    status: SessionStatus = SessionStatus.ACTIVE
    creator_id: str = ""
    channel_type: str = "console"
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "creator_id": self.creator_id,
            "channel_type": self.channel_type,
            "tags": self.tags,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Session":
        status = data.get("status", "active")
        if isinstance(status, str):
            status = SessionStatus(status)
        return cls(
            session_id=data["session_id"],
            status=status or SessionStatus.ACTIVE,
            creator_id=data.get("creator_id", ""),
            channel_type=data.get("channel_type", "console"),
            tags=data.get("tags", []),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            metadata=data.get("metadata", {}),
        )


@dataclass
class EventRecord:
    event_id: str
    session_id: str
    event_index: int
    timestamp: float
    event_type: str
    content: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "event_index": self.event_index,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "EventRecord":
        return cls(
            event_id=data["event_id"],
            session_id=data["session_id"],
            event_index=data["event_index"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            content=data["content"],
            metadata=data.get("metadata", {}),
        )
