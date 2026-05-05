"""
ToolRegistry —— 工具注册表，线程安全的工具增删查执行容器。

============================================================================
设计意图
============================================================================

  工具注册表是 Sandbox 的核心组件，负责:
    1. 注册工具（register）: 将 BaseTool 实例按名称索引
    2. 查找工具（get / has）: 按名称查找工具实例
    3. 移除工具（unregister）: 动态移除已注册的工具
    4. 列出工具（list_all / list_definitions）: 获取工具列表或 OpenAI 格式定义
    5. 执行工具（execute）: 安全执行工具并统一返回 ToolResult

============================================================================
线程安全
============================================================================

  所有对 _tools 字典的读写操作都通过 RLock 保护。
  sandbox.py 中的 Sandbox 也持有锁，双重锁保证了并发场景下的安全性。

============================================================================
错误处理
============================================================================

  execute() 方法内部捕获所有异常，包装为 ToolResult(success=False, error=...)
  不会向上层抛出原始异常，保证编排层不会因工具执行错误而崩溃。
"""
from typing import Dict, List, Optional, Any
from threading import RLock
from sandbox.tools.base import BaseTool, ToolDefinition, ToolResult
from common.errors import ToolNotFoundError


class ToolRegistry:
    """工具注册表 —— 线程安全的工具容器。

    Attributes:
        _tools: 工具名称 → BaseTool 实例的映射。
        _lock: 可重入锁，保证并发安全。
    """

    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}
        self._lock = RLock()

    # ── 注册 / 注销 ──

    def register(self, tool: BaseTool) -> None:
        """注册一个工具实例。

        如果工具名称已存在，会覆盖旧工具（幂等注册）。

        Args:
            tool: BaseTool 实例。
        """
        with self._lock:
            self._tools[tool.name] = tool

    def unregister(self, tool_name: str) -> bool:
        """注销一个工具。

        Args:
            tool_name: 要移除的工具名称。

        Returns:
            True 表示移除成功，False 表示工具不存在。
        """
        with self._lock:
            if tool_name in self._tools:
                del self._tools[tool_name]
                return True
            return False

    # ── 查询 ──

    def get(self, tool_name: str) -> BaseTool:
        """按名称获取工具实例。

        Args:
            tool_name: 工具名称。

        Returns:
            BaseTool 实例。

        Raises:
            ToolNotFoundError: 工具未注册。
        """
        with self._lock:
            tool = self._tools.get(tool_name)
            if not tool:
                raise ToolNotFoundError(tool_name)
            return tool

    def has(self, tool_name: str) -> bool:
        """检查工具是否已注册。

        Args:
            tool_name: 工具名称。

        Returns:
            True 表示工具已注册。
        """
        with self._lock:
            return tool_name in self._tools

    def list_all(self) -> List[BaseTool]:
        """返回所有已注册工具的列表。"""
        with self._lock:
            return list(self._tools.values())

    def list_definitions(self) -> List[Dict[str, Any]]:
        """返回所有工具的 OpenAI function calling 格式定义列表。

        供 call_model_activity 中注入到 LLM 请求的 tools 参数。
        """
        with self._lock:
            return [tool.get_openai_format() for tool in self._tools.values()]

    # ── 执行 ──

    async def execute(self, tool_name: str, arguments: Dict[str, Any]) -> ToolResult:
        """安全执行指定工具。

        流程:
          1. 按名称查找工具（不存在则抛出 ToolNotFoundError）
          2. 调用 tool.execute(**arguments)
          3. 如果返回值已是 ToolResult，直接返回
          4. 否则包装为 ToolResult(success=True, output=result)
          5. 任何异常都被捕获并包装为 ToolResult(success=False, error=...)

        Args:
            tool_name: 工具名称。
            arguments: 工具参数字典。

        Returns:
            ToolResult 实例（保证不抛出原始异常）。

        Raises:
            ToolNotFoundError: 工具未注册（仅在查找阶段，异常不会被捕获）。
        """
        tool = self.get(tool_name)
        try:
            result = await tool.execute(**arguments)
            # 如果工具自身返回了 ToolResult，直接使用
            if isinstance(result, ToolResult):
                return result
            # 否则包装为成功结果
            return ToolResult(success=True, output=result)
        except Exception as e:
            # 所有执行异常统一包装，不向上泄漏
            return ToolResult(success=False, error=str(e))

    def clear(self) -> None:
        """清空所有已注册工具（用于沙箱销毁时清理）。"""
        with self._lock:
            self._tools.clear()
