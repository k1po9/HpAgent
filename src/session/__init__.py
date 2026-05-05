"""
Session —— 记忆层（Memory Layer），当前处于抽空/存根状态。

============================================================================
历史变更
============================================================================

  旧版 Session 模块包含了完整的存储逻辑（FileSessionRepository、
  FileEventRepository、PostgresSessionRepository、PostgresEventRepository 等）。
  这些存储实现已被提取到 src/storage/ 中，通过 typing.Protocol 重新设计。

  旧版 SessionManager（约 330 行，包含会话 CRUD + 事件读写 + 便利方法）
  已被移除。原功能由以下替代:
    - 持久化存储 → src/storage/（file.py / postgres.py / _memory.py）
    - 会话元数据 → Temporal Workflow 的事件历史
    - 事件查询   → TemporalSessionManager（通过 Workflow Query 读取）

============================================================================
当前可用
============================================================================

  TemporalSessionManager  — 通过 Temporal Workflow Query 读取事件历史
                            （不依赖本地存储，直接查询运行中的 Workflow）
  Session                  — 会话领域实体（数据类）
  EventRecord              — 事件记录实体（数据类）
  SessionStatus            — 会话状态枚举（ACTIVE / ARCHIVED / COMPLETED）

============================================================================
未来规划
============================================================================

  记忆层将在 src/storage/ 之上重建，提供:
    - 会话摘要（SessionSummary）：自动压缩历史对话
    - 记忆提取（MemoryExtraction）：从对话中提取长期记忆
    - 上下文窗口管理：智能裁剪超出 token 限制的历史事件
"""
from .session_manager import TemporalSessionManager
from .models import Session, EventRecord, SessionStatus

__all__ = [
    "TemporalSessionManager",
    "Session",
    "EventRecord",
    "SessionStatus",
]
