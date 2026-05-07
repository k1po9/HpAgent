"""
Sandbox —— 单个沙箱实例，实现 ISandbox 接口。

============================================================================
设计意图
============================================================================

  每个 Sandbox 是一个独立的工具执行环境，包含:
    - 工具注册表（ToolRegistry）：管理工具元数据（供 LLM 发现）
    - nsjail 执行器（NsjailExecutor）：通过 nsjail 子进程隔离执行工具
    - 资源字典（_resources）：沙箱内共享资源
    - 生命周期状态：active → destroyed

  工具执行不再直接在进程内调用 tool.execute()，而是:
    tool_name + arguments → NsjailExecutor → nsjail subprocess → runner.py → stdout JSON

  线程安全: 所有状态修改通过 RLock 保护。

============================================================================
使用示例
============================================================================

  config = NsjailConfig(chroot_path="/sandbox", time_limit=30)
  executor = NsjailExecutor(config, redis_cache)
  sandbox = Sandbox(executor=executor, tools=[calculator_tool])
  result = await sandbox.execute("calculator", {"expression": "2+2"})
  definitions = await sandbox.list_tools()
"""
from typing import Dict, Any, List, Optional
from threading import RLock
import uuid
import time
import os
from .tools.base import BaseTool, ToolResult
from .tools.registry import ToolRegistry
from .nsjail import NsjailExecutor
from common.interfaces import ISandbox
from common.errors import ToolNotFoundError


class Sandbox(ISandbox):
    """单个工具执行沙箱 —— 工具发现 + nsjail 隔离执行 + 生命周期管理。

    Attributes:
        sandbox_id: 沙箱唯一标识（UUID）。
        _tool_registry: 工具注册表实例（存储元数据，供 LLM 发现工具）。
        _executor: nsjail 执行器（统一子进程执行入口）。
        _resources: 沙箱内共享资源字典。
        _created_at: 创建时间戳（用于空闲回收判断）。
        _last_used: 最后使用时间戳（每次 execute 时更新）。
        _status: 沙箱状态（"active" / "destroyed"）。
        _lock: 可重入锁，保证并发安全。
    """

    def __init__(
        self,
        executor: NsjailExecutor,
        sandbox_id: Optional[str] = None,
        tools: Optional[List[BaseTool]] = None,
        resources: Optional[Dict[str, Any]] = None,
    ):
        """初始化沙箱。

        Args:
            executor: nsjail 执行器实例（必须）。
            sandbox_id: 自定义沙箱 ID，None 则自动生成 UUID。
            tools: 初始工具列表，逐个注册到 ToolRegistry（仅元数据）。
            resources: 初始资源字典。
        """
        self.sandbox_id = sandbox_id or str(uuid.uuid4())
        self._executor = executor
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
        """通过 nsjail 子进程隔离执行指定工具。

        每次执行都会更新 _last_used 时间戳。

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            ToolResult 实例。

        Raises:
            ToolNotFoundError: 工具未注册。
        """
        with self._lock:
            self._last_used = time.time()
        if not self._tool_registry.has(tool_name):
            raise ToolNotFoundError(tool_name)
        result = await self._executor.execute(tool_name, arguments)
        return result

    async def list_tools(self) -> List[Dict[str, Any]]:
        """返回所有已注册工具的 OpenAI 格式定义列表。

        供 LLM 调用时填入 tools 参数。
        工具元数据仍由 ToolRegistry 管理，仅执行路径改为 nsjail。
        """
        return self._tool_registry.list_definitions()

    async def health_check(self) -> bool:
        """沙箱健康检查 —— 验证状态 + nsjail 二进制可用性。"""
        if self._status != "active":
            return False
        if not os.path.isfile(self._executor.config.nsjail_binary):
            return False
        return True

    # ── 工具管理 ──

    def register_tool(self, tool: BaseTool) -> None:
        """动态注册一个工具（仅注册元数据，执行通过 nsjail）。"""
        self._tool_registry.register(tool)

    def unregister_tool(self, tool_name: str) -> bool:
        """动态移除一个工具。

        Returns:
            True 表示移除成功，False 表示工具不存在。
        """
        return self._tool_registry.unregister(tool_name)

    def get_tool(self, tool_name: str) -> BaseTool:
        """按名称获取工具元数据实例。

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
