"""
Common 层 —— 所有模块共享的基础类型、接口和错误。

三个子模块：
  - types.py: 核心数据类型（Event, UnifiedMessage, ToolCall 等 dataclass）
  - interfaces.py: 抽象接口（IResources, IChannel）
  - errors.py: 统一错误体系（AgentError 及其子类）
"""
from .types import *
from .interfaces import *
from .errors import *

__all__ = [
    # types
    "Event",
    "EventType",
    "ChannelType",
    "StopReason",
    "ErrorSeverity",
    "UnifiedMessage",
    "ToolCall",
    "ToolResult",
    "ModelResponse",
    "SessionMetadata",
    # interfaces
    "IResources",
    "IChannel",
    # errors
    "AgentError",
    "SessionNotFoundError",
    "SandboxNotFoundError",
    "ToolNotFoundError",
    "ToolExecutionError",
    "ModelAPIError",
    "ValidationError",
]
