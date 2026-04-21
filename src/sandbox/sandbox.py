from typing import Dict, Any, List, Optional
from threading import RLock
import uuid
import time
from .tools.base import BaseTool, ToolResult
from .tools.registry import ToolRegistry
from ..common.interfaces import ISandbox
from ..common.errors import ToolNotFoundError


class Sandbox(ISandbox):
    def __init__(self, sandbox_id: Optional[str] = None, tools: Optional[List[BaseTool]] = None, resources: Optional[Dict[str, Any]] = None):
        self.sandbox_id = sandbox_id or str(uuid.uuid4())
        self._tool_registry = ToolRegistry()
        self._resources = resources or {}
        self._created_at = time.time()
        self._last_used = time.time()
        self._status = "active"
        self._lock = RLock()
        if tools:
            for tool in tools:
                self._tool_registry.register(tool)

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        with self._lock:
            self._last_used = time.time()
        if not self._tool_registry.has(tool_name):
            raise ToolNotFoundError(tool_name)
        result = await self._tool_registry.execute(tool_name, arguments)
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        return self._tool_registry.list_definitions()

    async def health_check(self) -> bool:
        return self._status == "active"

    def register_tool(self, tool: BaseTool) -> None:
        self._tool_registry.register(tool)

    def unregister_tool(self, tool_name: str) -> bool:
        return self._tool_registry.unregister(tool_name)

    def get_tool(self, tool_name: str) -> BaseTool:
        return self._tool_registry.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        return self._tool_registry.has(tool_name)

    @property
    def status(self) -> str:
        return self._status

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def last_used(self) -> float:
        return self._last_used

    def destroy(self) -> None:
        with self._lock:
            self._status = "destroyed"
            self._tool_registry.clear()

    def get_info(self) -> Dict[str, Any]:
        return {"sandbox_id": self.sandbox_id, "status": self._status, "created_at": self._created_at, "last_used": self._last_used, "tools_count": len(self._tool_registry.list_all()), "tools": [tool.name for tool in self._tool_registry.list_all()]}
