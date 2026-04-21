from typing import Any, Callable, Awaitable, Optional
from dataclasses import dataclass, field
from .protocol import Tool, ToolType
from .native.base import NativeTool
from .mcp.mcp_proxy import MCPProxyTool


@dataclass
class ToolFactory:
    """工具工厂：Agent自主生成原生/MCP工具"""
    
    async def create_native_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        execute_func: Callable[..., Awaitable[Any]],
    ) -> NativeTool:
        """创建原生工具"""
        
        class DynamicNativeTool(NativeTool):
            name = name
            description = description
            parameters = parameters
            
            async def _execute_impl(self, **kwargs) -> Any:
                return await execute_func(**kwargs)
        
        return DynamicNativeTool()
    
    async def create_mcp_tool(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        server_name: str,
        mcp_method: str,
    ) -> MCPProxyTool:
        """创建MCP工具代理"""
        return MCPProxyTool(
            name=name,
            description=description,
            parameters=parameters,
            server_name=server_name,
            mcp_method=mcp_method,
        )
