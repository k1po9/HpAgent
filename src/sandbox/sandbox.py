"""
Sandbox —— 单个沙箱实例，实现 ISandbox 接口。

============================================================================
设计意图
============================================================================

  每个 Sandbox 是一个独立的工具执行环境，包含:
    - 工具注册表（ToolRegistry）：管理可用工具
    - 资源字典（_resources）：沙箱内共享资源（如文件句柄、子进程）
    - 生命周期状态：active → destroyed

  线程安全: 所有状态修改通过 RLock 保护，支持并发的工具注册 / 执行。

============================================================================
使用示例
============================================================================

  sandbox = Sandbox(tools=[calculator_tool, search_tool])
  result = await sandbox.execute("calculator", {"expression": "2+2"})
  definitions = await sandbox.list_tools()  # 返回 OpenAI 格式的工具列表
  sandbox.destroy()  # 清理所有工具
"""
from typing import Dict, Any, List, Optional
from threading import RLock
import uuid
import time
from .tools.base import BaseTool, ToolResult
from .tools.registry import ToolRegistry
from common.interfaces import ISandbox
from common.errors import ToolNotFoundError


class Sandbox(ISandbox):
    """单个工具执行沙箱 —— 工具注册 + 执行隔离 + 生命周期管理。

    Attributes:
        sandbox_id: 沙箱唯一标识（UUID）。
        _tool_registry: 工具注册表实例。
        _resources: 沙箱内共享资源字典。
        _created_at: 创建时间戳（用于空闲回收判断）。
        _last_used: 最后使用时间戳（每次 execute 时更新）。
        _status: 沙箱状态（"active" / "destroyed"）。
        _lock: 可重入锁，保证并发安全。
    """

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        tools: Optional[List[BaseTool]] = None,
        resources: Optional[Dict[str, Any]] = None,
    ):
        """初始化沙箱。

        Args:
            sandbox_id: 自定义沙箱 ID，None 则自动生成 UUID。
            tools: 初始工具列表，逐个注册到 ToolRegistry。
            resources: 初始资源字典。
        """
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

    # ── 核心操作（ISandbox 接口实现） ──

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """执行指定工具。

        每次执行都会更新 _last_used 时间戳，供空闲回收策略参考。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            工具执行结果（ToolResult 或原始返回值）。

        Raises:
            ToolNotFoundError: 工具未注册。
        """
        with self._lock:
            self._last_used = time.time()
        if not self._tool_registry.has(tool_name):
            raise ToolNotFoundError(tool_name)
        result = await self._tool_registry.execute(tool_name, arguments)
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        """返回所有已注册工具的 OpenAI 格式定义列表。

        供 LLM 调用时填入 tools 参数。
        """
        return self._tool_registry.list_definitions()

    async def health_check(self) -> bool:
        """沙箱健康检查 —— 当前仅检查状态是否为 active。"""
        return self._status == "active"

    # ── 工具管理 ──

    def register_tool(self, tool: BaseTool) -> None:
        """动态注册一个工具（运行时扩展）。"""
        self._tool_registry.register(tool)

    def unregister_tool(self, tool_name: str) -> bool:
        """动态移除一个工具。

        Returns:
            True 表示移除成功，False 表示工具不存在。
        """
        return self._tool_registry.unregister(tool_name)

    def get_tool(self, tool_name: str) -> BaseTool:
        """按名称获取工具实例。

        Raises:
            ToolNotFoundError: 工具未注册。
        """
        return self._tool_registry.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        """检查工具是否已注册。"""
        return self._tool_registry.has(tool_name)

    # ── 属性访问 ──

    @property
    def status(self) -> str:
        """沙箱当前状态。"""
        return self._status

    @property
    def created_at(self) -> float:
        """沙箱创建时间戳。"""
        return self._created_at

    @property
    def last_used(self) -> float:
        """沙箱最后使用时间戳（用于空闲回收）。"""
        return self._last_used

    # ── 生命周期 ──

    def destroy(self) -> None:
        """销毁沙箱: 标记状态 + 清空工具注册表。"""
        with self._lock:
            self._status = "destroyed"
            self._tool_registry.clear()

    def get_info(self) -> Dict[str, Any]:
        """返回沙箱元信息摘要（供管理端查询）。"""
        return {
            "sandbox_id": self.sandbox_id,
            "status": self._status,
            "created_at": self._created_at,
            "last_used": self._last_used,
            "tools_count": len(self._tool_registry.list_all()),
            "tools": [tool.name for tool in self._tool_registry.list_all()],
        }
