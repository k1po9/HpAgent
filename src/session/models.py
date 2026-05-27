"""
Session 领域实体。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List


class SessionStatus(str, Enum):
    """会话状态。"""
    ACTIVE = "active"
    COMPLETED = "completed"


@dataclass
class Session:
    """会话实体 —— 一次完整的对话交互 + 文件系统工作区。

    Attributes:
        session_id: 会话唯一标识。
        account_id: 统一用户账号 ID（等于 workspace user_uuid）。
        status: 会话当前状态。
        creator_id: 会话创建者 ID（渠道原始 sender_id）。
        channel_type: 会话来源渠道（"napcat" / "console" / "web"）。
        task_summary: 任务摘要。
        session_dir: sessions/<id>/ 相对路径。
        plan_file: execution/plan.yaml 相对路径。
        conversation_file: conversation/messages.jsonl 相对路径。
        output_dir: workspace/output/ 相对路径。
        tags: 标签列表。
        created_at: Unix 时间戳。
        updated_at: Unix 时间戳。
        metadata: 附加元数据字典。
    """
    session_id: str
    account_id: str = ""
    status: SessionStatus = SessionStatus.ACTIVE
    creator_id: str = ""
    channel_type: str = "console"
    task_summary: str = ""
    session_dir: str = ""
    plan_file: str = ""
    conversation_file: str = ""
    output_dir: str = ""
    tags: List[str] = field(default_factory=list)
    created_at: float = 0.0
    updated_at: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "account_id": self.account_id,
            "status": self.status.value,
            "creator_id": self.creator_id,
            "channel_type": self.channel_type,
            "task_summary": self.task_summary,
            "session_dir": self.session_dir,
            "plan_file": self.plan_file,
            "conversation_file": self.conversation_file,
            "output_dir": self.output_dir,
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
            account_id=data.get("account_id", ""),
            status=status or SessionStatus.ACTIVE,
            creator_id=data.get("creator_id", ""),
            channel_type=data.get("channel_type", "console"),
            task_summary=data.get("task_summary", ""),
            session_dir=data.get("session_dir", ""),
            plan_file=data.get("plan_file", ""),
            conversation_file=data.get("conversation_file", ""),
            output_dir=data.get("output_dir", ""),
            tags=data.get("tags", []),
            created_at=data.get("created_at", 0.0),
            updated_at=data.get("updated_at", 0.0),
            metadata=data.get("metadata", {}),
        )
