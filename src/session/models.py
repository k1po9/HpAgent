"""
Session Models —— 会话层的领域实体定义。

============================================================================
领域模型
============================================================================

  SessionStatus  — 会话状态枚举（ACTIVE / ARCHIVED / COMPLETED）
  Session        — 会话实体（代表一次完整的对话交互）
  EventRecord    — 事件记录实体（会话中的单条事件，存储在数据库中）

============================================================================
与 common.types.Event 的区别
============================================================================

  common.types.Event:
    - 运行时使用的事件对象（Temporal Workflow 内部流转）
    - 包含 EventType 枚举（user_message / assistant_response / tool_call / tool_result）

  session.models.EventRecord:
    - 持久化存储的事件条目
    - event_type 为字符串（而非枚举），便于跨系统兼容
    - 包含 event_index 字段用于排序和回溯

  两者通过 to_dict() / from_dict() 实现互相转换。
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional
from enum import Enum
import time


class SessionStatus(str, Enum):
    """会话状态枚举。

    ACTIVE:    活跃状态，会话正在进行中，可以接收新消息。
    ARCHIVED:  已归档，会话被存档保留，不再接收新消息。
    COMPLETED: 已完成，会话正常结束（用户主动结束或达到轮次上限）。
    """
    ACTIVE = "active"
    ARCHIVED = "archived"
    COMPLETED = "completed"


@dataclass
class Session:
    """会话实体 —— 表示一次完整的对话交互。

    一个 Session 可以跨越多个渠道（QQ + Web），
    通过 account_id 关联到统一用户账号。

    Attributes:
        session_id: 会话唯一标识。
        account_id: 统一用户账号 ID（跨渠道关联，由 AccountService 解析）。
        status: 会话当前状态（ACTIVE / ARCHIVED / COMPLETED）。
        creator_id: 会话创建者 ID（渠道原始 sender_id）。
        channel_type: 会话来源渠道（如 "napcat" / "console" / "web"）。
        tags: 会话标签，用于分类和检索（如 ["support", "urgent"]）。
        created_at: 会话创建时间戳（Unix timestamp）。
        updated_at: 会话最后更新时间戳。
        metadata: 会话附加元数据（如渠道上下文、用户偏好等）。
    """
    session_id: str
    account_id: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    creator_id: str = ""
    channel_type: str = "console"
    tags: List[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典（用于存储或 API 响应）。"""
        return {
            "session_id": self.session_id,
            "account_id": self.account_id,
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
        """从字典反序列化（从存储或 API 请求中恢复）。

        兼容 status 字段的字符串和枚举两种格式。
        """
        status = data.get("status", "active")
        if isinstance(status, str):
            status = SessionStatus(status)
        return cls(
            session_id=data["session_id"],
            account_id=data.get("account_id", ""),
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
    """事件记录 —— 持久化存储到数据库的事件条目。

    与 common.types.Event（运行时对象）不同，EventRecord 是存储层的数据结构，
    包含 event_index 用于排序，event_type 为字符串以保持跨系统兼容。

    Attributes:
        event_id: 事件唯一标识。
        session_id: 所属会话 ID。
        event_index: 事件在会话中的序号（从 1 开始递增，用于排序和回溯）。
        timestamp: 事件发生时间戳（Unix timestamp）。
        event_type: 事件类型字符串（如 "user_message" / "assistant_response" /
                    "tool_call" / "tool_result"）。
        content: 事件核心数据（字典格式，结构因 event_type 而异）。
        metadata: 事件附加元数据。
    """
    event_id: str
    session_id: str
    event_index: int
    timestamp: float
    event_type: str
    content: Dict[str, Any]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典。"""
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
        """从字典反序列化。"""
        return cls(
            event_id=data["event_id"],
            session_id=data["session_id"],
            event_index=data["event_index"],
            timestamp=data["timestamp"],
            event_type=data["event_type"],
            content=data["content"],
            metadata=data.get("metadata", {}),
        )
