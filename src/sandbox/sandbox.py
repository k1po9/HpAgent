"""
Sandbox —— 模型"手"层：接收 workspace 路径，持有 ToolRegistry，按类别路由执行。

设计原则:
  - Sandbox 接收外部传入的 workspace 路径
  - 本地工具（native）在 workspace 绑定的环境中进程内执行
  - Bash 工具可选通过 nsjail 加固
  - MCP 工具远端执行，Skill 工具展开为子调用
"""
from typing import Dict, Any, List, Optional
from threading import RLock
import uuid
import time

from sandbox.tools.types import ToolResult
from sandbox.tools.registry import ToolRegistry
from common.interfaces import ISandbox
from common.errors import ToolNotFoundError


class Sandbox(ISandbox):
    """工具的 workspace 绑定执行环境。

    接收 workspace 路径 + ToolRegistry，按工具类别路由执行:
      native  → 进程内执行（workspace 已绑定到工具闭包）
      mcp     → MCP 远端调用（通过 tool.ainvoke）
      skill   → Skill 流水线展开（通过 tool.ainvoke）
    """

    def __init__(
        self,
        workspace_path: str,
        tool_registry: ToolRegistry,
        sandbox_id: Optional[str] = None,
        nsjail_executor=None,
    ):
        self._workspace = workspace_path
        self._registry = tool_registry
        self._nsjail = nsjail_executor
        self.sandbox_id = sandbox_id or str(uuid.uuid4())
        self._created_at = time.time()
        self._last_used = time.time()
        self._status = "active"
        self._lock = RLock()

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        with self._lock:
            self._last_used = time.time()

        tool = self._registry.get(tool_name)
        if tool is None:
            return ToolResult(
                success=False,
                error=f"Tool '{tool_name}' not found",
            )
        category = self._registry.get_category(tool_name)

        if category == "native" and self._nsjail and tool_name == "Bash":
            try:
                return await self._nsjail.execute(tool_name, arguments)
            except Exception as e:
                return ToolResult(success=False, error=str(e))

        try:
            result = await tool.ainvoke(arguments)
            if isinstance(result, ToolResult):
                return result
            output = result.content if hasattr(result, "content") else str(result)
            return ToolResult(success=True, output=output)
        except ToolNotFoundError:
            raise
        except Exception as e:
            return ToolResult(
                success=False,
                error=str(e),
            )

    async def list_tools(self) -> List[Dict[str, Any]]:
        return self._registry.list_for_llm()

    async def health_check(self) -> bool:
        return self._status == "active"

    @property
    def status(self) -> str:
        return self._status

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def workspace_path(self) -> str:
        return self._workspace

    def destroy(self) -> None:
        with self._lock:
            self._status = "destroyed"

    def get_info(self) -> Dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "status": self._status,
            "workspace": self._workspace,
            "created_at": self._created_at,
            "last_used": self._last_used,
            "tools_count": len(self._registry.list_all()),
            "tools": [t.name for t in self._registry.list_all()],
        }
