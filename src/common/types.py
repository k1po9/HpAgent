from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
import uuid
import time


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    MODEL_MESSAGE = "model_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    CONFIG_CHANGE = "config_change"
    SESSION_START = "session_start"
    SESSION_COMPLETE = "session_complete"
    SESSION_ARCHIVED = "session_archived"
    LOOP_STARTED = "loop_started"
    LOOP_COMPLETED = "loop_completed"
    TURN_COMPLETED = "turn_completed"


class ChannelType(str, Enum):
    TELEGRAM = "telegram"
    DISCORD = "discord"
    SLACK = "slack"
    WECHAT = "wechat"
    WEB = "web"
    CONSOLE = "console"


class StopReason(str, Enum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    REFUSAL = "refusal"
    ERROR = "error"


class ErrorSeverity(str, Enum):
    RECOVERABLE = "recoverable"
    FATAL = "fatal"


@dataclass
class Event:
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    timestamp: float = field(default_factory=time.time)
    event_type: EventType = EventType.USER_MESSAGE
    content: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value if isinstance(self.event_type, Enum) else self.event_type,
            "content": self.content,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Event":
        event_type = data.get("event_type")
        if isinstance(event_type, str):
            event_type = EventType(event_type)
        return cls(
            event_id=data.get("event_id", str(uuid.uuid4())),
            session_id=data.get("session_id", ""),
            timestamp=data.get("timestamp", time.time()),
            event_type=event_type or EventType.USER_MESSAGE,
            content=data.get("content", {}),
            metadata=data.get("metadata", {}),
        )


@dataclass
class UnifiedMessage:
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    session_id: str = ""
    sender_id: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)
    media_urls: List[str] = field(default_factory=list)

    def to_event(self) -> Event:
        return Event(
            session_id=self.session_id,
            event_type=EventType.USER_MESSAGE,
            content={
                "message_id": self.message_id,
                "sender_id": self.sender_id,
                "channel_type": self.channel_type.value if isinstance(self.channel_type, Enum) else self.channel_type,
                "content": self.content,
                "media_urls": self.media_urls,
            },
            metadata=self.metadata,
        )


@dataclass
class ToolCall:
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ToolCall":
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            name=data.get("name", ""),
            arguments=data.get("arguments", {}),
        )


@dataclass
class ToolResult:
    tool_call_id: str
    status: str
    content: Any = ""
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "status": self.status,
            "content": self.content,
            "error": self.error,
        }


@dataclass
class ModelResponse:
    content: Optional[str] = None
    tool_calls: Optional[List[ToolCall]] = None
    stop_reason: StopReason = StopReason.END_TURN
    usage: Optional[Dict[str, Any]] = None


@dataclass
class SessionMetadata:
    session_id: str
    creator_id: str = ""
    channel_type: ChannelType = ChannelType.CONSOLE
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "creator_id": self.creator_id,
            "channel_type": self.channel_type.value if isinstance(self.channel_type, Enum) else self.channel_type,
            "tags": self.tags,
            "created_at": self.created_at,
            "status": self.status,
        }
