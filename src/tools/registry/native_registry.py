from typing import Optional
from dataclasses import dataclass, field
from ..protocol import Tool, ToolType


@dataclass
class NativeToolRegistry:
    """原生工具注册器"""
    _tools: dict[str, Tool] = field(default_factory=dict)
    
    def register(self, tool: Tool) -> None:
        if tool.tool_type != ToolType.NATIVE:
            raise ValueError(f"Tool {tool.name} is not a native tool")
        self._tools[tool.name] = tool
    
    def unregister(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        return False
    
    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)
    
    def list_all(self) -> list[Tool]:
        return list(self._tools.values())
    
    def list_definitions(self) -> list[dict]:
        return [
            {
                "name": t.name,
                "description": t.description,
                "input_schema": t.parameters,
                "type": "native"
            }
            for t in self._tools.values()
        ]
