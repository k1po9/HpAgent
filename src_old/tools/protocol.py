from typing import Protocol, runtime_checkable, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ToolType(str, Enum):
    NATIVE = "native"
    MCP = "mcp"


class Tool(Protocol):
    """统一工具协议（原生 + MCP）"""
    name: str
    description: str
    parameters: dict[str, Any]
    tool_type: ToolType
    
    async def execute(self, **kwargs) -> Any: ...


@dataclass
class Skill:
    """技能策略协议"""
    name: str
    description: str
    bound_tool_name: Optional[str] = None
    instructions: str = ""
    constraints: dict[str, Any] = field(default_factory=dict)
    
    async def apply(self, tool_call: dict) -> dict:
        """应用技能策略到工具调用"""
        if self.instructions:
            return {"modified": True, "instructions": self.instructions}
        return {"modified": False}
