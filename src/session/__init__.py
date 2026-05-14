"""
Session —— 会话领域实体 + 存储。

提供:
  - Session / SessionStatus: 领域数据类
  - SessionStore: 会话存储层（Redis 热数据 + Hindsight 长期记忆 + 本地文件备份）

SessionStore 只向 Harness 层暴露。
Harness 通过 SessionStore 读写会话事件流 + 召回/存储长期记忆。
"""
from .models import Session, SessionStatus
from .store import SessionStore

__all__ = [
    "Session",
    "SessionStatus",
    "SessionStore",
]
