"""
统一错误体系 —— 所有业务异常的基础类型。

设计原则：
  1. 所有错误继承自 AgentError，携带 code + message + recoverable + details。
  2. code 使用 ErrorCode 枚举，支持程序化分类处理（重试 / 返回 4xx / 5xx）。
  3. recoverable 标记错误是否可恢复：True → 可重试，False → 需人工介入。
  4. details 携带字段级上下文（如 session_id、tool_name），便于日志和调试。

用法::

    raise SessionNotFoundError("abc123")
    raise ToolExecutionError("calculator", "Division by zero")
"""
from typing import Optional, Dict, Any
from enum import Enum


class ErrorCode(str, Enum):
    """错误码枚举 —— 每个错误码对应一个可恢复/不可恢复的业务场景。

    上层根据 code 决定处理策略：
      - SESSION_NOT_FOUND / VALIDATION_ERROR → 返回 4xx 给调用方
      - TOOL_EXECUTION_FAILED / MODEL_API_ERROR → 重试或降级
      - INTERNAL_ERROR → 告警 + 记录日志
    """
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"          # 会话不存在
    SESSION_ARCHIVED = "SESSION_ARCHIVED"            # 会话已归档，不可写入
    EVENT_NOT_FOUND = "EVENT_NOT_FOUND"              # 事件未找到
    SANDBOX_NOT_FOUND = "SANDBOX_NOT_FOUND"          # 沙箱不存在
    SANDBOX_UNHEALTHY = "SANDBOX_UNHEALTHY"          # 沙箱健康检查失败
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"                # 工具未注册
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"  # 工具执行失败
    MODEL_API_ERROR = "MODEL_API_ERROR"              # 模型 API 调用失败
    CREDENTIAL_NOT_FOUND = "CREDENTIAL_NOT_FOUND"    # 凭据未找到
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"        # 凭据已过期
    ORCHESTRATION_ERROR = "ORCHESTRATION_ERROR"      # 编排错误
    HARNESS_ERROR = "HARNESS_ERROR"                  # 大脑层错误
    VALIDATION_ERROR = "VALIDATION_ERROR"            # 输入验证失败
    NETWORK_ERROR = "NETWORK_ERROR"                  # 网络错误
    INTERNAL_ERROR = "INTERNAL_ERROR"                # 内部未知错误


class AgentError(Exception):
    """所有业务异常的基类。

    Attributes:
        code: 错误码枚举值。
        message: 人类可读的错误描述。
        recoverable: True 表示可重试恢复，False 表示不可恢复（需人工介入或终止流程）。
        details: 附加的上下文字典（如 {"session_id": "abc"}），用于日志和调试。
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        recoverable: bool = True,
        details: Optional[Dict[str, Any]] = None,
    ):
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")

    def to_dict(self) -> Dict[str, Any]:
        """序列化为字典，便于通过 API 返回给客户端。"""
        return {
            "code": self.code.value,
            "message": self.message,
            "recoverable": self.recoverable,
            "details": self.details,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# 预定义子类 —— 每个子类封装了特定场景的默认 code 和 recoverable 策略
# ═══════════════════════════════════════════════════════════════════════════════

class SessionNotFoundError(AgentError):
    """会话不存在 —— 不可恢复，调用方应创建新会话。"""
    def __init__(self, session_id: str):
        super().__init__(
            code=ErrorCode.SESSION_NOT_FOUND,
            message=f"Session not found: {session_id}",
            recoverable=False,
            details={"session_id": session_id},
        )


class SandboxNotFoundError(AgentError):
    """沙箱不存在 —— 可恢复，调用方可创建新沙箱后重试。"""
    def __init__(self, sandbox_id: str):
        super().__init__(
            code=ErrorCode.SANDBOX_NOT_FOUND,
            message=f"Sandbox not found: {sandbox_id}",
            recoverable=True,
            details={"sandbox_id": sandbox_id},
        )


class ToolNotFoundError(AgentError):
    """工具未注册 —— 可恢复，调用方可尝试其他工具。"""
    def __init__(self, tool_name: str):
        super().__init__(
            code=ErrorCode.TOOL_NOT_FOUND,
            message=f"Tool not found: {tool_name}",
            recoverable=True,
            details={"tool_name": tool_name},
        )


class ToolExecutionError(AgentError):
    """工具执行失败 —— 可恢复，调用方可重试或换工具。"""
    def __init__(self, tool_name: str, reason: str):
        super().__init__(
            code=ErrorCode.TOOL_EXECUTION_FAILED,
            message=f"Tool execution failed: {tool_name}",
            recoverable=True,
            details={"tool_name": tool_name, "reason": reason},
        )


class ModelAPIError(AgentError):
    """模型 API 调用失败 —— 可恢复，调用方可按退避链切换模型。"""
    def __init__(self, reason: str, status_code: Optional[int] = None):
        super().__init__(
            code=ErrorCode.MODEL_API_ERROR,
            message=f"Model API error: {reason}",
            recoverable=True,
            details={"reason": reason, "status_code": status_code},
        )


class ValidationError(AgentError):
    """输入验证失败 —— 不可恢复，调用方需修正输入后重试。"""
    def __init__(self, field: str, reason: str):
        super().__init__(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Validation error on field '{field}': {reason}",
            recoverable=False,
            details={"field": field, "reason": reason},
        )
