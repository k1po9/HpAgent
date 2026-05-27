"""ToolRegistry —— 三槽位工具注册中心，基于 LangChain BaseTool。"""
from threading import RLock
from typing import Dict, List, Optional

from langchain_core.tools import BaseTool

from sandbox.tools.types import ToolResult


class ToolRegistry:
    """工具注册中心 —— 三槽位（native / mcp / skill）+ freeze 封禁。

    三槽位:
      _native_tools: 本地 Python 工具
      _mcp_tools:    MCP 协议工具
      _skills:       Skills 复合工具

    RAG 层:
      _retriever: ToolRetriever 实例，支持语义检索动态注入
    """

    def __init__(self, retriever=None):
        self._native_tools: Dict[str, BaseTool] = {}
        self._mcp_tools: Dict[str, BaseTool] = {}
        self._skills: Dict[str, BaseTool] = {}
        self._lock = RLock()
        self._frozen = False
        self._retriever = retriever

    # ── 注册 / 注销 ─────────────────────────────────────────────

    def register(self, tool: BaseTool, category: str = "native") -> None:
        if self._frozen:
            raise RuntimeError("ToolRegistry is frozen")
        with self._lock:
            target = {
                "native": self._native_tools,
                "mcp": self._mcp_tools,
                "skill": self._skills,  
            }[category]
            target[tool.name] = tool

    def unregister(self, name: str) -> bool:
        with self._lock:
            for d in (self._native_tools, self._mcp_tools, self._skills):
                if name in d:
                    del d[name]
                    return True
            return False

    def freeze(self) -> None:
        self._frozen = True

    # ── 查询 ────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[BaseTool]:
        with self._lock:
            for d in (self._native_tools, self._mcp_tools, self._skills):
                if name in d:
                    return d[name]
            return None

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def get_category(self, name: str) -> Optional[str]:
        with self._lock:
            if name in self._native_tools:
                return "native"
            if name in self._mcp_tools:
                return "mcp"
            if name in self._skills:
                return "skill"
        return None

    def list_all(self) -> List[BaseTool]:
        with self._lock:
            return (
                list(self._native_tools.values())
                + list(self._mcp_tools.values())
                + list(self._skills.values())
            )

    # ── LLM 工具格式输出 ────────────────────────────────────────

    def list_for_llm(self) -> List[dict]:
        """所有工具的 OpenAI function calling 格式。"""
        tools = self.list_all()
        result = []
        for t in tools:
            if hasattr(t, "to_openai_function"):
                result.append(t.to_openai_function())
            else:
                result.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.args_schema.model_json_schema() if t.args_schema else {},
                    },
                })
        return result

    # ── RAG 动态检索 ────────────────────────────────────────────

    async def retrieve_for_query(self, query: str, top_k: int = 5) -> List[BaseTool]:
        if self._retriever is None:
            return self.list_all()
        return await self._retriever.retrieve(query, top_k=top_k, registry=self)

    async def retrieve_for_llm(self, query: str, top_k: int = 5) -> List[dict]:
        if self._retriever is None:
            return self.list_for_llm()
        return await self._retriever.retrieve_for_llm(
            query, registry=self, top_k=top_k
        )

    # ── 执行 ────────────────────────────────────────────────────

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found")
        try:
            result = await tool.ainvoke(arguments)
            if isinstance(result, ToolResult):
                return result
            output = result.content if hasattr(result, "content") else str(result)
            return ToolResult(success=True, output=output)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def clear(self) -> None:
        with self._lock:
            self._native_tools.clear()
            self._mcp_tools.clear()
            self._skills.clear()
