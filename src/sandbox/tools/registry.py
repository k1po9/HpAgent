"""Worker declaration catalog and Execution-bound tool registry/executor."""
import hashlib
import json
import logging
import time
from copy import deepcopy
from dataclasses import asdict
from threading import RLock
from typing import Any, Dict, List, Optional

from langchain_core.tools import BaseTool

from common.logging import exception_fields, log_event
from sandbox.tools.routing.models import RegisteredTool, ToolKind, ToolRoutingSpec
from sandbox.tools.types import ToolResult

logger = logging.getLogger("HpAgent.ToolRegistry")


def _tool_definition_payload(tool: BaseTool, routing: ToolRoutingSpec | None = None) -> dict:
    """Mechanical declaration projection; never retain the executable wrapper."""
    payload = {
        "version": 2,
        "name": tool.name,
        "description": tool.description or "",
        "parameters": (tool.args_schema if isinstance(tool.args_schema, dict)
                       else tool.args_schema.model_json_schema() if tool.args_schema else {}),
        "metadata": dict(tool.metadata or {}),
    }
    if routing is not None:
        payload["routing"] = {
            key: sorted(value) if isinstance(value, frozenset) else value
            for key, value in asdict(routing).items()
        }
    # Only JSON declarations may cross this boundary (no closures or identities).
    return json.loads(json.dumps(payload, ensure_ascii=False))


def _tool_definition_hash(definition: dict | BaseTool) -> str:
    payload = definition if isinstance(definition, dict) else _tool_definition_payload(definition)
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class ToolDefinitionCatalog:
    """Worker-owned declarations, independent of Execution binding/authorization."""

    def __init__(self):
        self._definitions: dict[str, dict] = {}
        self._incomplete_sources: set[str] = set()

    def add(self, tool: BaseTool, *, category: str, routing: ToolRoutingSpec) -> None:
        if tool.name in self._definitions:
            raise ValueError(f"duplicate tool name: {tool.name}")
        payload = _tool_definition_payload(tool, routing)
        payload["category"] = ToolKind(category).value
        self._definitions[tool.name] = payload

    def mark_incomplete(self, source: str) -> None:
        self._incomplete_sources.add(source)

    @property
    def complete(self) -> bool:
        return not self._incomplete_sources

    def snapshot(self) -> list[dict]:
        return deepcopy([self._definitions[name] for name in sorted(self._definitions)])


class ToolRegistry:
    """Execution-local tools with unique names, execution and freeze semantics."""

    def __init__(self, *, log_context: dict | None = None):
        self._log_context = dict(log_context or {})
        self._tools: Dict[str, RegisteredTool] = {}
        self._lock = RLock()
        self._frozen = False

    # ── 注册 / 注销 ─────────────────────────────────────────────

    def _ensure_mutable(self) -> None:
        if self._frozen:
            raise RuntimeError("ToolRegistry is frozen")

    def register(self, tool: BaseTool, category: str = "native", *, routing: ToolRoutingSpec | None = None) -> None:
        with self._lock:
            self._ensure_mutable()
            if tool.name.startswith(("capability__", "result__")):
                raise ValueError(f"reserved Harness tool name: {tool.name}")
            if tool.name in self._tools:
                raise ValueError(f"duplicate tool name: {tool.name}")
            if routing is None:
                raise ValueError(f"tool '{tool.name}' is missing ToolRoutingSpec")
            self._tools[tool.name] = RegisteredTool(tool, ToolKind(category), routing)

    def unregister(self, name: str) -> bool:
        with self._lock:
            self._ensure_mutable()
            return self._tools.pop(name, None) is not None

    def freeze(self) -> None:
        with self._lock:
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
        started = time.monotonic()
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"Tool '{tool_name}' not found")
        try:
            result = await tool.ainvoke(arguments)
            if isinstance(result, ToolResult):
                return result
            output = result.content if hasattr(result, "content") else result
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
            log_event(logger, logging.ERROR,
                      "tool_invocation_failed", "tools", **self._log_context,
                      tool=tool_name, status="failed",
                      elapsed_ms=round((time.monotonic() - started) * 1000),
                      **exception_fields(e))
            return ToolResult(success=False, error=str(e))

    def clear(self) -> None:
        with self._lock:
            self._ensure_mutable()
            self._tools.clear()
