from typing import Optional, Dict, Any
from enum import Enum


class ErrorCode(str, Enum):
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_ARCHIVED = "SESSION_ARCHIVED"
    EVENT_NOT_FOUND = "EVENT_NOT_FOUND"
    SANDBOX_NOT_FOUND = "SANDBOX_NOT_FOUND"
    SANDBOX_UNHEALTHY = "SANDBOX_UNHEALTHY"
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    MODEL_API_ERROR = "MODEL_API_ERROR"
    CREDENTIAL_NOT_FOUND = "CREDENTIAL_NOT_FOUND"
    CREDENTIAL_EXPIRED = "CREDENTIAL_EXPIRED"
    ORCHESTRATION_ERROR = "ORCHESTRATION_ERROR"
    HARNESS_ERROR = "HARNESS_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NETWORK_ERROR = "NETWORK_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AgentError(Exception):
    def __init__(self, code: ErrorCode, message: str, recoverable: bool = True, details: Optional[Dict[str, Any]] = None):
        self.code = code
        self.message = message
        self.recoverable = recoverable
        self.details = details or {}
        super().__init__(f"[{code.value}] {message}")

    def to_dict(self) -> Dict[str, Any]:
        return {"code": self.code.value, "message": self.message, "recoverable": self.recoverable, "details": self.details}


class SessionNotFoundError(AgentError):
    def __init__(self, session_id: str):
        super().__init__(code=ErrorCode.SESSION_NOT_FOUND, message=f"Session not found: {session_id}", recoverable=False, details={"session_id": session_id})


class SandboxNotFoundError(AgentError):
    def __init__(self, sandbox_id: str):
        super().__init__(code=ErrorCode.SANDBOX_NOT_FOUND, message=f"Sandbox not found: {sandbox_id}", recoverable=True, details={"sandbox_id": sandbox_id})


class ToolNotFoundError(AgentError):
    def __init__(self, tool_name: str):
        super().__init__(code=ErrorCode.TOOL_NOT_FOUND, message=f"Tool not found: {tool_name}", recoverable=True, details={"tool_name": tool_name})


class ToolExecutionError(AgentError):
    def __init__(self, tool_name: str, reason: str):
        super().__init__(code=ErrorCode.TOOL_EXECUTION_FAILED, message=f"Tool execution failed: {tool_name}", recoverable=True, details={"tool_name": tool_name, "reason": reason})


class ModelAPIError(AgentError):
    def __init__(self, reason: str, status_code: Optional[int] = None):
        super().__init__(code=ErrorCode.MODEL_API_ERROR, message=f"Model API error: {reason}", recoverable=True, details={"reason": reason, "status_code": status_code})


class ValidationError(AgentError):
    def __init__(self, field: str, reason: str):
        super().__init__(code=ErrorCode.VALIDATION_ERROR, message=f"Validation error on field '{field}': {reason}", recoverable=False, details={"field": field, "reason": reason})
