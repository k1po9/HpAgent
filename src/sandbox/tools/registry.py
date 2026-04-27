from typing import Dict, List, Optional, Any
from threading import RLock
from sandbox.tools.base import BaseTool, ToolDefinition, ToolResult
from common.errors import ToolNotFoundError


class ToolRegistry:
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._lock = RLock()

    def register(self, tool: BaseTool) -> None:
        with self._lock:
            self._tools[tool.name] = tool

    def unregister(self, tool_name: str) -> bool:
        with self._lock:
            if tool_name in self._tools:
                del self._tools[tool_name]
                return True
            return False

    def get(self, tool_name: str) -> BaseTool:
        with self._lock:
            tool = self._tools.get(tool_name)
            if not tool:
                raise ToolNotFoundError(tool_name)
            return tool

    def has(self, tool_name: str) -> bool:
        with self._lock:
            return tool_name in self._tools

    def list_all(self) -> List[BaseTool]:
        with self._lock:
            return list(self._tools.values())

    def list_definitions(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [tool.get_openai_format() for tool in self._tools.values()]

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        tool = self.get(tool_name)
        try:
            result = await tool.execute(**arguments)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def clear(self) -> None:
        with self._lock:
            self._tools.clear()
