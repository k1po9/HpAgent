from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum


class ToolType(str, Enum):
    NATIVE = "native"
    MCP = "mcp"
    SKILL = "skill"


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    tool_type: ToolType = ToolType.NATIVE
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_openai_format(self) -> Dict[str, Any]:
        return {"type": "function", "function": {"name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class ToolResult:
    success: bool = True
    output: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"success": self.success, "output": self.output, "error": self.error, "metadata": self.metadata}


class BaseTool(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> Dict[str, Any]: ...

    @property
    def tool_type(self) -> ToolType:
        return ToolType.NATIVE

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.name, description=self.description, parameters=self.parameters, tool_type=self.tool_type)

    def get_openai_format(self) -> Dict[str, Any]:
        return self.get_definition().to_openai_format()
