from typing import Any
from abc import ABC, abstractmethod
from ..protocol import Tool, ToolType


class NativeTool(Tool, ABC):
    """原生工具基类"""
    
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    tool_type: ToolType = ToolType.NATIVE
    
    async def execute(self, **kwargs) -> Any:
        return await self._execute_impl(**kwargs)
    
    @abstractmethod
    async def _execute_impl(self, **kwargs) -> Any:
        """实际执行逻辑，由子类实现"""
        pass
