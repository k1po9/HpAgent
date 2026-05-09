"""
Workspace 数据模型 —— 用户、会话、产出的纯数据 dataclass。

这些模型不包含任何 IO 逻辑，仅定义数据结构和序列化方法。
所有持久化逻辑在 manager.py 和 db.py 中。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional


class SessionStatus(StrEnum):
    """会话状态枚举。

    ACTIVE:   会话正在进行中，Agent 可读写 workspace。
    COMPLETED: 会话正常结束，产出物保留在 output/。
    FAILED:   会话异常终止，workspace 保留现场供排查。
    DELETED:  会话已被清理任务移除目录（仅保留 DB 记录）。
    """
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"


@dataclass
class User:
    """工作区用户 —— 对应一个独立的工作目录。

    Attributes:
        uuid: 用户唯一标识（UUID 字符串）。
        username: 可读用户名。
        profile_path: user_profile.yaml 的相对路径。
        persistent_dir: persistent/ 目录的相对路径。
        created_at: ISO 8601 创建时间戳字符串。
    """
    uuid: str
    username: str = ""
    profile_path: str = ""
    persistent_dir: str = ""
    created_at: str = ""


@dataclass
class Session:
    """工作区会话 —— 对应一次完整的 Agent 交互。

    Attributes:
        session_id: 会话唯一标识。
        user_uuid: 所属用户 UUID。
        status: 当前状态（active/completed/failed/deleted）。
        task_summary: 任务摘要（由 Agent 自动生成或用户指定）。
        session_dir: sessions/<session_id>/ 相对于 user 根目录的路径。
        plan_file: execution/plan.yaml 的相对路径。
        conversation_file: conversation/messages.jsonl 的相对路径。
        output_dir: workspace/output/ 的相对路径。
        tags: 标签列表（用于检索和分类）。
        metadata_json: 附加元数据 JSON 字符串。
        created_at: ISO 8601 创建时间戳。
        updated_at: ISO 8601 最后更新时间戳。
    """
    session_id: str
    user_uuid: str
    status: SessionStatus = SessionStatus.ACTIVE
    task_summary: str = ""
    session_dir: str = ""
    plan_file: str = ""
    conversation_file: str = ""
    output_dir: str = ""
    tags: list[str] = field(default_factory=list)
    metadata_json: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class Artifact:
    """会话产出物 —— 工作目录中生成文件的索引记录。

    文件实际内容存储在文件系统中，此表仅记录路径和类型。
    避免每次列举产出都扫描文件系统。

    Attributes:
        artifact_id: 产出物唯一标识。
        session_id: 所属会话 ID。
        file_path: 相对于 workspace/ 的文件路径。
        file_type: 文件类型（如 "image/png", "text/markdown"）。
        file_size: 文件大小（字节）。
        checksum: SHA-256 校验和。
        created_at: ISO 8601 创建时间戳。
    """
    artifact_id: str
    session_id: str
    file_path: str
    file_type: str = ""
    file_size: int = 0
    checksum: str = ""
    created_at: str = ""
