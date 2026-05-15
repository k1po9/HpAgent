"""
ToolFactory —— 工具工厂，用于动态创建工具实例。

============================================================================
两种创建方式
============================================================================

  1. create_tool(name, description, parameters, execute_func)
     → 动态创建工具（无需定义新类，适合简单函数）

  2. create_*_tool() 系列静态方法
     → 创建内置工具（calculator / web_search / file_read）
     → create_default_tools() 返回所有内置工具的列表

============================================================================
DynamicTool
============================================================================

  BaseTool 的具体实现，通过构造函数注入 name / description / parameters / execute_func。
  避免为每个简单工具创建一个新类，减少样板代码。

============================================================================
安全注意事项
============================================================================

  calculator 工具使用 eval()，但已限制 __builtins__ 为空字典，
  仅允许纯数学表达式，不能执行任意 Python 代码。
  生产环境建议替换为安全的数学表达式解析器（如 numexpr / asteval）。
"""
from typing import Any, Callable, Awaitable, Dict
from .base import BaseTool, ToolType


class DynamicTool(BaseTool):
    """动态工具 —— 通过函数引用构造，无需定义新工具类。

    适用场景:
      - 简单的一次性工具（如计算器、搜索）
      - 从配置文件动态加载的工具
      - 测试时 mock 的工具

    Attributes:
        _name: 工具名称。
        _description: 工具功能描述。
        _parameters: 参数 JSON Schema。
        _execute_func: 执行函数引用（async callable）。
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        execute_func: Callable[..., Awaitable[Any]],
    ):
        """创建动态工具。

        Args:
            name: 工具名称。
            description: 工具功能描述。
            parameters: 参数 JSON Schema。
            execute_func: 异步执行函数，签名为 async def func(**kwargs) -> Any。
        """
        self._name = name
        self._description = description
        self._parameters = parameters
        self._execute_func = execute_func

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> Dict[str, Any]:
        return self._parameters

    @property
    def tool_type(self) -> ToolType:
        return ToolType.NATIVE

    async def execute(self, **kwargs) -> Any:
        """委托给注入的执行函数。

        Args:
            **kwargs: 工具参数。

        Returns:
            执行函数的返回值。
        """
        return await self._execute_func(**kwargs)


class ToolFactory:
    """工具工厂 —— 提供动态工具创建和内置工具构建的静态方法。

    所有方法均为静态方法，无需实例化。
    """

    @staticmethod
    def create_tool(
        name: str,
        description: str,
        parameters: Dict[str, Any],
        execute_func: Callable[..., Awaitable[Any]],
    ) -> BaseTool:
        """通用工具创建方法 —— 根据参数动态构造一个工具实例。

        Args:
            name: 工具名称。
            description: 工具功能描述。
            parameters: 参数 JSON Schema。
            execute_func: 异步执行函数。

        Returns:
            一个 DynamicTool 实例。
        """
        return DynamicTool(
            name=name,
            description=description,
            parameters=parameters,
            execute_func=execute_func,
        )

    @staticmethod
    def create_calculator_tool() -> BaseTool:
        """创建计算器工具 —— 安全地计算数学表达式。

        安全措施: __builtins__ 设为空字典，阻止执行任意 Python 代码。
        仅支持纯数学运算（+、-、*、/、**、% 等）。

        Returns:
            calculator 工具实例。
        """
        async def execute(expression: str) -> str:
            try:
                # __builtins__={} 防止任意代码执行
                result = eval(expression, {"__builtins__": {}}, {})
                return str(result)
            except Exception as e:
                return f"Error: {str(e)}"

        return DynamicTool(
            name="calculator",
            description="Evaluate a mathematical expression",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Math expression",
                    }
                },
                "required": ["expression"],
            },
            execute_func=execute,
        )

    @staticmethod
    def create_search_tool() -> BaseTool:
        """创建 Web 搜索工具 —— 当前为占位实现（返回模拟数据）。

        TODO: 接入真实的搜索 API（如 Bing / Google / SerpAPI）。

        Returns:
            web_search 工具实例。
        """
        async def execute(query: str, limit: int = 5) -> Dict[str, Any]:
            return {
                "query": query,
                "results": [
                    {"title": f"Result {i+1} for {query}", "url": f"https://example.com/{i}"}
                    for i in range(min(limit, 3))
                ],
            }

        return DynamicTool(
            name="web_search",
            description="Search the web",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5},
                },
                "required": ["query"],
            },
            execute_func=execute,
        )

    @staticmethod
    def create_file_read_tool() -> BaseTool:
        """创建文件读取工具 —— 读取本地文件内容。

        安全注意: 当前未做路径沙箱限制，生产环境需限制可读目录。

        Returns:
            file_read 工具实例。
        """
        async def execute(file_path: str) -> str:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"Error reading file: {str(e)}"

        return DynamicTool(
            name="file_read",
            description="Read file contents",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string"},
                },
                "required": ["file_path"],
            },
            execute_func=execute,
        )

    @staticmethod
    def create_default_tools() -> list[BaseTool]:
        """创建默认工具集 —— 包含 calculator、web_search、file_read。

        Worker 启动时通过此方法获取初始工具集，
        注册到 SandboxManager 创建的默认沙箱中。

        Returns:
            默认工具实例列表。
        """
        return [
            ToolFactory.create_calculator_tool(),
            ToolFactory.create_search_tool(),
            ToolFactory.create_file_read_tool(),
        ]
