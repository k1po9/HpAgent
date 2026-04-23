from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import time


class SessionStatus(str, Enum):
    """会话状态枚举"""
    ACTIVE = "active"          # 活跃状态，会话正在进行中
    ARCHIVED = "archived"      # 已归档，会话被存档保留
    COMPLETED = "completed"    # 已完成，会话正常结束


@dataclass
class Session:
    """会话实体，表示一次完整的对话交互"""
    session_id: str                                                # 会话唯一标识
    status: SessionStatus = SessionStatus.ACTIVE                   # 会话当前状态
    creator_id: str = ""                                           # 会话创建者 ID
    channel_type: str = "console"                                  # 会话来源渠道（与 ChannelType 对应）
    tags: List[str] = field(default_factory=list)                  # 会话标签，用于分类和检索
    created_at: float = field(default_factory=time.time)           # 会话创建时间戳
    updated_at: float = field(default_factory=time.time)           # 会话最后更新时间戳
    metadata: Dict[str, Any] = field(default_factory=dict)         # 会话附加元数据

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
    """事件记录，持久化存储到数据库的事件条目"""
    event_id: str                    # 事件唯一标识
    session_id: str                  # 所属会话 ID
    event_index: int                 # 事件在会话中的序号（用于排序和回溯）
    timestamp: float                 # 事件发生时间戳
    event_type: str                  # 事件类型（对应 EventType 的字符串值）
    content: Dict[str, Any]          # 事件核心数据
    metadata: Dict[str, Any] = field(default_factory=dict)  # 事件附加元数据

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
