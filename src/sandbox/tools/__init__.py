"""
Tools Layer —— 基于 LangChain 生态的 HpAgent 工具体系。

公共 API:
  ToolRegistry    — 三槽位工具注册中心（native / mcp / skill）
  ToolResult      — HpAgent 统一工具执行返回值
  ToolVectorStore — ChromaDB 工具向量存储
  ToolRetriever   — 语义检索器（按用户意图动态注入工具）
"""
from sandbox.tools.types import ToolResult
from sandbox.tools.registry import ToolRegistry

__all__ = [
    "ToolResult",
    "ToolRegistry",
]
