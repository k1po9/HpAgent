"""
HpAgent 工具系统保留类型 —— ToolResult 统一返回值。

LangChain BaseTool 返回 str / ToolMessage，HpAgent 保留此类型
以携带结构化元数据（metadata、error context）。
"""
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class ToolResult:
    """工具执行统一返回值 —— HpAgent 保留类型。

    LangChain 的 BaseTool 内部返回 str/ToolMessage，
    此类型统一包装为结构化结果，携带 metadata 和 error 上下文。
    """
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    suggestion: Optional[str] = None
    metadata: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "suggestion": self.suggestion,
            "metadata": self.metadata,
        }
        return {k: v for k, v in d.items() if v is not None}
