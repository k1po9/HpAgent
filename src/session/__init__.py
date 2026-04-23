"""
Session 模块 - 数据层实现

提供：
- SessionManager: ISession 接口实现
- Session, EventRecord: 数据模型
- FileSessionRepository, FileEventRepository: 持久化仓库
"""
from .session_manager import SessionManager
from .models import Session, EventRecord, SessionStatus
from .repositories import FileSessionRepository, FileEventRepository

__all__ = [
    "SessionManager",
    "Session",
    "EventRecord",
    "SessionStatus",
    "FileSessionRepository",
    "FileEventRepository",
]