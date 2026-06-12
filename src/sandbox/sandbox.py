"""
Sandbox —— 模型"手"层：工具选择 + 安全执行 + 输出后处理 + 跨轮状态。

设计原则:
  - 工具选择由 Sandbox 全权负责（hints 优先 → RAG → required 合并）
  - 执行按类别路由：native 进程内 / Bash nsjail / MCP 远端 / Skill 展开
  - 输出截断在 Sandbox 统一执行（HarnessRunner 无需关心工具输出长度）
  - 跨轮 hints 状态归属于 Sandbox（生命周期与 session 一致）
  - 审计信息以纯数据 dict 返回（不依赖 Harness 层的 Event 体系）
"""
from typing import Dict, Any, List, Optional, Tuple
import logging
import time
import uuid

from sandbox.tools.types import ToolResult
from sandbox.tools.registry import ToolRegistry

logger = logging.getLogger("HpAgent.Sandbox")


class Sandbox:
    """工具的 workspace 绑定执行环境。

    职责:
      1. select_tools() — 完整工具选择管线（hints → RAG → required 合并 → 排序）
      2. execute()      — 安全路由执行 + 输出截断
      3. 持有 hints 队列 — 跨轮次工具检索偏好
      4. 返回审计信息 — 供 HarnessRunner 写入事件日志
    """

    def __init__(
        self,
        workspace_path: str,
        tool_registry: ToolRegistry,
        sandbox_id: Optional[str] = None,
        nsjail_executor=None,
        truncation_threshold: int = 50000,
        max_merged_multiplier: float = 1.5,
    ):
        self._workspace = workspace_path
        self._registry = tool_registry
        self._nsjail = nsjail_executor
        self._truncation_threshold = truncation_threshold
        self._max_merged_multiplier = max_merged_multiplier

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
        """完整工具选择管线：用户 query 为主 + hints 为辅 → RAG → required 合并 → 去重 → 升序排列。

        Args:
            query: 用户消息（始终作为主 RAG query）
            top_k: RAG 检索上限

        Returns:
            (llm_tool_dicts, audit_info)
            llm_tool_dicts: 注入 next_tool_hint 后的 OpenAI function calling 格式
            audit_info: {mode, queries, tool_count, tools} 供审计用
        """
        # 1. 取消费 hints（用后即清），用户 query 始终排在首位
        hints = self._drain_hints()
        queries = ([query] if query else []) + hints

        # 2. RAG 检索（max_merged = top_k × 配置的合并缓冲系数）
        max_merged = int(top_k * self._max_merged_multiplier)
        result = await self._registry.retrieve_for_llm_multi(queries, top_k, max_merged)

        # 3. 合并 required 工具（去重，required 优先排在最前，最终不超过 top_k）
        required = self._registry.list_required_for_llm()
        if required:
            existing = set(self._registry.get_tool_names(result))
            for rd in reversed(required):
                name = self._registry._extract_tool_name(rd)
                if name not in existing:
                    result.insert(0, rd)
                    existing.add(name)
            # 合并后以 top_k 为硬上界截断：required 已前置，超出部分为 RAG 低相关度工具
            if len(result) > top_k:
                trimmed_names = self._registry.get_tool_names(result[top_k:])
                logger.debug("select_tools: trimmed %d tools beyond top_k=%d: %s",
                           len(trimmed_names), top_k, trimmed_names)
                result = result[:top_k]

        # 3.5 按相关性分数升序排列（低分在前，高分在后）
        scores: dict[str, float] = {}
        if self._registry._retriever is not None:
            scores = getattr(self._registry._retriever, "last_scores", {})
        if scores:
            result.sort(key=lambda d: scores.get(self._registry._extract_tool_name(d), 0.0))

        # 4. 审计信息（含相关性评分）
        retrieval_mode = "rag_multi" if len(queries) > 1 else "rag" if queries else "full"
        tool_names = self._registry.get_tool_names(result)
        audit = {
            "mode": retrieval_mode,
            "limit": top_k,
            "queries": queries[:5],
            "tool_count": len(result),
            "tools": tool_names,
            "scores": {name: scores.get(name, 0.0) for name in tool_names},
        }

        return result, audit

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
        """清空所有累积的 hints。每个新轮次开始时由 HarnessRunner 调用，
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

    # ── 生命周期 ──────────────────────────────────────────────────────────

    async def list_tools(self) -> List[Dict[str, Any]]:
        return self._registry.list_for_llm()

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
