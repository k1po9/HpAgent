"""
Session Models —— 会话层的领域实体定义。

============================================================================
领域模型
============================================================================

  SessionStatus  — 会话状态枚举（ACTIVE / ARCHIVED / COMPLETED）
  Session        — 会话实体（代表一次完整的对话交互）
"""
from dataclasses import dataclass, field
from typing import Dict, Any, List
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
        account_id: 统一用户账号 ID（跨渠道关联）。
        status: 会话当前状态（ACTIVE / ARCHIVED / COMPLETED）。
        creator_id: 会话创建者 ID（渠道原始 sender_id）。
        channel_type: 会话来源渠道（如 "napcat" / "console" / "web"）。
        tags: 会话标签，用于分类和检索。
        created_at: 会话创建时间戳（Unix timestamp）。
        updated_at: 会话最后更新时间戳。
        metadata: 会话附加元数据。
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
        """序列化为字典。"""
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
        """从字典反序列化。"""
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
