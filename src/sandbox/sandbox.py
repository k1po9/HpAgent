"""
Sandbox —— 模型"手"层：工具选择 + 安全执行 + 输出后处理 + 跨轮状态。

设计原则:
  - 工具选择委托给 capability-first ToolRouter
  - 执行按类别路由：native 进程内 / Bash nsjail / MCP 远端 / Skill 展开
  - 输出截断在 Sandbox 统一执行（Agent loop 无需关心工具输出长度）
  - 跨轮 hints 状态归属于 Sandbox（生命周期与 session 一致）
  - 审计信息以纯数据 dict 返回（不依赖 Harness 层的 Event 体系）
"""
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from sandbox.tools.registry import ToolRegistry
from sandbox.tools.types import ToolResult
from sandbox.tools.routing.router import ToolRouter

logger = logging.getLogger("HpAgent.Sandbox")


class Sandbox:
    """工具的 workspace 绑定执行环境。

    职责:
      1. select_tools() — 收集 hints/runtime facts 并委托 ToolRouter
      2. execute()      — 安全路由执行 + 输出截断
      3. 持有 hints 队列 — 跨轮次工具检索偏好
      4. 返回审计信息 — 供 execution audit sink 写入事件日志
    """

    def __init__(
        self,
        workspace_path: str,
        tool_registry: ToolRegistry,
        sandbox_id: Optional[str] = None,
        nsjail_executor=None,
        truncation_threshold: int = 50000,
        tool_router: ToolRouter | None = None,
        selection_context_provider=None,
    ):
        self._workspace = workspace_path
        self._registry = tool_registry
        self._nsjail = nsjail_executor
        self._truncation_threshold = truncation_threshold
        self._tool_router = tool_router or ToolRouter(tool_registry)
        self._selection_context_provider = selection_context_provider

        # 跨轮状态：hints 队列
        self._hints: List[str] = []

        # 生命周期
        self.sandbox_id = sandbox_id or str(uuid.uuid4())
        self._created_at = time.time()
        self._last_used = time.time()
        self._status = "active"

    # ── 工具选择 ──────────────────────────────────────────────────────────

    async def select_tools(
        self, query: str, top_k: int = 8
    ) -> Tuple[List[dict], dict]:
        """Delegate tool selection using current runtime facts and optional hints.

        Args:
            query: 用户消息（始终作为主 RAG query）
            top_k: 最终候选工具数量上限

        Returns:
            (llm_tool_dicts, audit_info)
            llm_tool_dicts: 注入 next_tool_hint 后的 OpenAI function calling 格式
            audit_info: {mode, queries, tool_count, tools} 供审计用
        """
        hints = self._drain_hints()
        if self._selection_context_provider is None:
            raise RuntimeError("tool selection context provider is unavailable")
        selected = await self._tool_router.select(
            query=query, hints=hints,
            runtime=self._selection_context_provider.snapshot(), final_limit=top_k,
        )
        return list(selected.tool_schemas), selected.audit

    # ── 工具执行 ──────────────────────────────────────────────────────────

    async def execute(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Tuple[ToolResult, dict]:
        """执行管线：hint 提取 → 安全路由 → 执行 → 截断。

        Returns:
            (tool_result, audit_info)
        """
        t0 = time.monotonic()
        self._last_used = time.time()

        # 1. 提取 hint（内部字段，不让工具看到）
        arguments = dict(arguments)
        hint = arguments.pop("next_tool_hint", None)

        # 2. 安全路由：nsjail 加固
        category = self._registry.get_category(tool_name)
        if category == "native" and self._nsjail and tool_name == "Bash":
            try:
                result = await self._nsjail.execute(tool_name, arguments)
            except Exception as e:
                result = ToolResult(success=False, error=str(e))
        else:
            result = await self._registry.execute(tool_name, arguments)

        # 3. 收集 hint 到队列
        if hint:
            self._hints.append(hint)

        # 4. 截断过长的输出
        truncated = False
        if isinstance(result.output, str) and len(result.output) > self._truncation_threshold:
            result.output = result.output[:self._truncation_threshold]
            result.metadata["truncated"] = True
            result.metadata["original_length"] = len(result.output)
            truncated = True

        elapsed_ms = (time.monotonic() - t0) * 1000

        audit = {
            "tool_name": tool_name,
            "latency_ms": round(elapsed_ms, 1),
            "success": result.success,
            "truncated": truncated,
        }

        if result.error:
            audit["error"] = result.error

        return result, audit

    # ── 跨轮 hints ────────────────────────────────────────────────────────

    def reset_hints(self) -> None:
        """清空所有累积的 hints。每个新轮次开始时由 Agent loop 调用，
        防止上一轮末尾产生的 hint 泄漏到下一轮的工具检索。"""
        self._hints.clear()

    def _drain_hints(self) -> List[str]:
        """取出并清空所有累积的 hints。每次 select_tools() 时调用。"""
        if not self._hints:
            return []
        drained = list(self._hints)
        self._hints.clear()
        return drained

    # ── 委托方法（ToolRegistry 的直接视图） ───────────────────────────────

    def get_tool_name(self, tool_dict: Dict[str, Any]) -> str:
        return self._registry._extract_tool_name(tool_dict)

    def get_tool_names(self, tool_dicts: List[Dict[str, Any]]) -> List[str]:
        return self._registry.get_tool_names(tool_dicts)

    def get_category(self, tool_name: str) -> Optional[str]:
        return self._registry.get_category(tool_name)

    def get_tool_metadata(self, tool_name: str) -> dict:
        return self._registry.get_metadata(tool_name)

    # ── 生命周期 ──────────────────────────────────────────────────────────

    async def list_tools(self) -> List[Dict[str, Any]]:
        from sandbox.tools.routing.projector import ToolSchemaProjector
        projector = ToolSchemaProjector()
        return [projector.project(item) for item in self._registry.list_registered()]

    async def health_check(self) -> bool:
        return self._status == "active"

    @property
    def status(self) -> str:
        return self._status

    @property
    def created_at(self) -> float:
        return self._created_at

    @property
    def last_used(self) -> float:
        return self._last_used

    @property
    def workspace_path(self) -> str:
        return self._workspace

    def destroy(self) -> None:
        self._status = "destroyed"

    def get_info(self) -> Dict[str, Any]:
        return {
            "sandbox_id": self.sandbox_id,
            "status": self._status,
            "workspace": self._workspace,
            "created_at": self._created_at,
            "last_used": self._last_used,
            "tools_count": len(self._registry.list_all()),
            "tools": [t.name for t in self._registry.list_all()],
        }
