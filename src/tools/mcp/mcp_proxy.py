from typing import Any
from dataclasses import dataclass, field
from ..native.base import NativeTool
from ..protocol import ToolType


@dataclass
class MCPProxyTool(NativeTool):
    """MCP工具代理（适配统一协议）"""
    
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""
    mcp_method: str = ""
    tool_type: ToolType = field(default_factory=lambda: ToolType.MCP)
    
    async def _execute_impl(self, **kwargs) -> Any:
        return {"error": "MCP execution not implemented", "server": self.server_name, "method": self.mcp_method}
