"""Canonical tool catalog and executor."""
import json
import logging
from threading import RLock
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool

from sandbox.tools.types import ToolResult
from sandbox.tools.routing.models import RegisteredTool, ToolKind, ToolRoutingSpec

logger = logging.getLogger("HpAgent.ToolRegistry")


class ToolRegistry:
    """Globally unique tool catalog with execution and freeze semantics."""

    def __init__(self):
        self._tools: Dict[str, RegisteredTool] = {}
        self._lock = RLock()
        self._frozen = False

    # ── 注册 / 注销 ─────────────────────────────────────────────

    def register(self, tool: BaseTool, category: str = "native", *, routing: ToolRoutingSpec | None = None) -> None:
        if self._frozen:
            raise RuntimeError("ToolRegistry is frozen")
        with self._lock:
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            if routing is None:
                raise ValueError(f"tool '{tool.name}' is missing ToolRoutingSpec")
            self._tools[tool.name] = RegisteredTool(tool, ToolKind(category), routing)

    def unregister(self, name: str) -> bool:
        with self._lock:
            return self._tools.pop(name, None) is not None

    def freeze(self) -> None:
        missing = [name for name, item in self._tools.items() if item.routing is None]
        if missing:
            raise ValueError(f"tools missing routing contracts: {missing}")
        self._frozen = True

    # ── 查询 ────────────────────────────────────────────────────

    def get(self, name: str) -> Optional[BaseTool]:
        with self._lock:
            item = self._tools.get(name)
            return item.tool if item else None

    def require(self, name: str) -> RegisteredTool:
        with self._lock:
            item = self._tools.get(name)
            if item is None:
                raise KeyError(name)
            return item

    def list_registered(self) -> List[RegisteredTool]:
        with self._lock:
            return list(self._tools.values())

    def has(self, name: str) -> bool:
        return self.get(name) is not None

    def get_category(self, name: str) -> Optional[str]:
        with self._lock:
            item = self._tools.get(name)
            return item.kind.value if item else None

    def get_metadata(self, name: str) -> dict:
        tool = self.get(name)
        return dict(getattr(tool, "metadata", {}) or {}) if tool else {}

    def list_all(self) -> List[BaseTool]:
        with self._lock:
            return [item.tool for item in self._tools.values()]

    def count_by_category(self) -> Dict[str, int]:
        """返回实际注册工具数量，供能力审计和准确日志使用。"""
        with self._lock:
            result = {kind.value: 0 for kind in ToolKind}
            for item in self._tools.values():
                result[item.kind.value] += 1
            return result

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
            metadata: dict[str, Any] = {}
            usage_fields = (getattr(tool, "metadata", None) or {}).get(
                "usage_json_fields"
            )
            if isinstance(usage_fields, dict) and isinstance(output, str):
                try:
                    payload = json.loads(output)
                except (json.JSONDecodeError, TypeError):
                    payload = None
                if isinstance(payload, dict):
                    usage: dict[str, int] = {}
                    for source, dimension in usage_fields.items():
                        amount = payload.get(source)
                        if isinstance(dimension, str) and isinstance(amount, int):
                            usage[dimension] = max(0, amount)
                    if usage:
                        metadata["budget_usage"] = usage
                    trace_fields = (getattr(tool, "metadata", None) or {}).get(
                        "trace_json_fields"
                    )
                    if isinstance(trace_fields, dict):
                        trace_metadata = {
                            target: payload[source]
                            for source, target in trace_fields.items()
                            if isinstance(target, str)
                            and isinstance(payload.get(source), (bool, int, float, str))
                        }
                        if trace_metadata:
                            metadata["trace_metadata"] = trace_metadata
            return ToolResult(success=True, output=output, metadata=metadata)
        except Exception as e:
            return ToolResult(success=False, error=str(e))

    def clear(self) -> None:
        with self._lock:
            self._tools.clear()
