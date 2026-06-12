"""ToolRegistry —— 三槽位工具注册中心，基于 LangChain BaseTool。"""
import logging
from threading import RLock
from typing import Dict, List, Optional

from langchain_core.tools import BaseTool

from sandbox.tools.types import ToolResult

logger = logging.getLogger("HpAgent.ToolRegistry")


class ToolRegistry:
    """工具注册中心 —— 三槽位（native / mcp / skill）+ freeze 封禁。

    三槽位:
      _native_tools: 本地 Python 工具
      _mcp_tools:    MCP 协议工具
      _skills:       Skills 复合工具

    RAG 层:
      _retriever: ToolRetriever 实例，支持语义检索动态注入
    """

    def __init__(self, retriever=None, per_query_min: int = 3):
        self._native_tools: Dict[str, BaseTool] = {}
        self._mcp_tools: Dict[str, BaseTool] = {}
        self._skills: Dict[str, BaseTool] = {}
        self._lock = RLock()
        self._frozen = False
        self._retriever = retriever
        self._per_query_min = per_query_min

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

    def list_required(self) -> List[BaseTool]:
        """返回所有标记为 required 的工具（始终加载，不参与 RAG 过滤）。"""
        with self._lock:
            result = []
            for d in (self._native_tools, self._mcp_tools, self._skills):
                for t in d.values():
                    meta = getattr(t, "metadata", {}) or {}
                    if meta.get("required"):
                        result.append(t)
            return result

    # ── LLM 工具格式输出 ────────────────────────────────────────

    _NEXT_TOOL_HINT_FIELD = {
        "type": "string",
        "description": (
            "简短预测：获得本工具结果后，下一步可能需要的工具类型或场景，"
            "用于后续工具检索，不超过30个词"
        ),
    }

    @staticmethod
    def _tool_to_llm_dict(tool: BaseTool) -> dict:
        """将 BaseTool 转为 OpenAI function calling 格式 dict。"""
        if hasattr(tool, "to_openai_function"):
            return tool.to_openai_function()
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.args_schema.model_json_schema() if tool.args_schema else {},
            },
        }

    @staticmethod
    def _extract_tool_name(tool_dict: dict) -> str:
        """从 LLM 格式 dict 提取工具名（兼容两种 schema 格式）。"""
        func = tool_dict.get("function", tool_dict)
        return func.get("name", "")

    @staticmethod
    def _inject_next_tool_hint(tool_dict: dict) -> dict:
        """向 OpenAI function schema 注入 next_tool_hint 必填字段。

        处理两种 schema 格式：
          A) {"name":..., "parameters":{...}}              (to_openai_function)
          B) {"type":"function", "function":{ name, parameters }}  (手动构建)
        """
        func = tool_dict.get("function", tool_dict)
        params = func.setdefault("parameters", {"type": "object", "properties": {}})
        props = params.setdefault("properties", {})
        required: list = params.setdefault("required", [])
        # 放在 properties 第一位
        new_props = {"next_tool_hint": ToolRegistry._NEXT_TOOL_HINT_FIELD}
        new_props.update(props)
        params["properties"] = new_props
        params["required"] = ["next_tool_hint"] + list(required)
        return tool_dict

    def list_for_llm(self, limit: int = 0) -> List[dict]:
        """所有工具的 OpenAI function calling 格式，可选 limit 截断（0=不限制）。"""
        tools = self.list_all()
        if limit and limit < len(tools):
            tools = tools[:limit]
        return [self._inject_next_tool_hint(self._tool_to_llm_dict(t)) for t in tools]

    # ── RAG 动态检索 ────────────────────────────────────────────

    async def retrieve_for_query(self, query: str, top_k: int = 5) -> List[BaseTool]:
        if self._retriever is None:
            tools = self.list_all()
            return tools[:top_k] if top_k < len(tools) else tools
        return await self._retriever.retrieve(query, top_k=top_k, registry=self)

    async def retrieve_for_llm(self, query: str, top_k: int = 5) -> List[dict]:
        if self._retriever is None:
            return self.list_for_llm(limit=top_k)
        results = await self._retriever.retrieve_for_llm(
            query, registry=self, top_k=top_k
        )
        return [self._inject_next_tool_hint(r) for r in results]

    async def retrieve_for_llm_multi(
        self, queries: List[str], top_k: int = 8, max_merged: int = 12
    ) -> List[dict]:
        """多 query 分别检索 + 去重合并 + 硬上限。

        单 query 直接委托 retrieve_for_llm()；
        多 query 每个检索 per_query_k = max(3, ceil(top_k / len))，合并去重后硬截断到 max_merged。
        """
        import math

        # 空 query → 走全量（受 top_k 保护）
        effective = [q for q in queries if q]
        logger.debug("retrieve_for_llm_multi: %d queries → %d effective, top_k=%d, max_merged=%d, retriever=%s",
                    len(queries), len(effective), top_k, max_merged, self._retriever is not None)
        if not effective:
            result = self.list_for_llm(limit=top_k)
            logger.debug("retrieve_for_llm_multi: empty → list_for_llm(limit=%d) → %d tools", top_k, len(result))
            return result

        # 单 query → 直接检索（受 max_merged 硬上限保护）
        if len(effective) == 1:
            result = await self.retrieve_for_llm(query=effective[0], top_k=top_k)
            if len(result) > max_merged:
                logger.warning("retrieve_for_llm_multi: single query returned %d > max_merged=%d, truncating",
                             len(result), max_merged)
                result = result[:max_merged]
            logger.debug("retrieve_for_llm_multi: single query → retrieve_for_llm(top_k=%d) → %d tools", top_k, len(result))
            return result

        # 多 query → 分头检索 + 去重合并
        per_query_k = max(self._per_query_min, math.ceil(top_k / len(effective)))
        merged: List[dict] = []
        seen: set = set()
        for q in effective:
            if len(merged) >= max_merged:
                break
            batch = await self.retrieve_for_llm(query=q, top_k=per_query_k)
            for tool in batch:
                name = self._extract_tool_name(tool)
                if name and name not in seen:
                    seen.add(name)
                    merged.append(tool)
                    if len(merged) >= max_merged:
                        break
        return merged[:max_merged]

    def list_required_for_llm(self) -> List[dict]:
        """Required 工具的 LLM 格式列表。"""
        return [self._inject_next_tool_hint(self._tool_to_llm_dict(t)) for t in self.list_required()]

    def get_tool_names(self, tool_dicts: List[dict]) -> List[str]:
        """从 LLM 格式 dict 列表提取所有工具名。"""
        return [self._extract_tool_name(t) for t in tool_dicts]

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
