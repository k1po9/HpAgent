from typing import Any, Callable, Awaitable, Dict
from .base import BaseTool, ToolType


class DynamicTool(BaseTool):
    def __init__(self, name: str, description: str, parameters: Dict[str, Any], execute_func: Callable[..., Awaitable[Any]]):
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
        return await self._execute_func(**kwargs)


class ToolFactory:
    @staticmethod
    def create_tool(name: str, description: str, parameters: Dict[str, Any], execute_func: Callable[..., Awaitable[Any]]) -> BaseTool:
        return DynamicTool(name=name, description=description, parameters=parameters, execute_func=execute_func)

    @staticmethod
    def create_calculator_tool() -> BaseTool:
        async def execute(expression: str) -> str:
            try:
                result = eval(expression, {"__builtins__": {}}, {})
                return str(result)
            except Exception as e:
                return f"Error: {str(e)}"
        return DynamicTool(name="calculator", description="Evaluate a mathematical expression", parameters={"type": "object", "properties": {"expression": {"type": "string", "description": "Math expression"}}, "required": ["expression"]}, execute_func=execute)

    @staticmethod
    def create_search_tool() -> BaseTool:
        async def execute(query: str, limit: int = 5) -> Dict[str, Any]:
            return {"query": query, "results": [{"title": f"Result {i+1} for {query}", "url": f"https://example.com/{i}"} for i in range(min(limit, 3))]}
        return DynamicTool(name="web_search", description="Search the web", parameters={"type": "object", "properties": {"query": {"type": "string"}, "limit": {"type": "integer", "default": 5}}, "required": ["query"]}, execute_func=execute)

    @staticmethod
    def create_file_read_tool() -> BaseTool:
        async def execute(file_path: str) -> str:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    return f.read()
            except Exception as e:
                return f"Error reading file: {str(e)}"
        return DynamicTool(name="file_read", description="Read file contents", parameters={"type": "object", "properties": {"file_path": {"type": "string"}}, "required": ["file_path"]}, execute_func=execute)

    @staticmethod
    def create_default_tools() -> list[BaseTool]:
        return [ToolFactory.create_calculator_tool(), ToolFactory.create_search_tool(), ToolFactory.create_file_read_tool()]
