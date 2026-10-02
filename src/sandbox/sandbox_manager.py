"""
SandboxManager —— 沙箱池管理器，按会话创建 workspace 绑定的沙箱。

核心职责:
  1. 创建: create_execution_sandbox() → 用 LOCAL_TOOL_FACTORIES 创建 workspace 绑定的本地工具
  2. 查询: get_sandbox_for_execution() / get_sandbox()
  3. 销毁: destroy_sandbox()
  4. 空闲回收: cleanup_idle_sandboxes()

每个会话一个 Sandbox，会话结束时销毁。
"""
import logging
import os
import time
import uuid
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from common.errors import SandboxNotFoundError
from sandbox.tools.local import LOCAL_TOOL_FACTORIES
from sandbox.tools.registry import ToolRegistry
from sandbox.tools.routing import ToolRouter, routing_for
from sandbox.tools.routing.context import ToolSelectionContextBuilder

from .nsjail import NsjailConfig, NsjailExecutor
from .sandbox import Sandbox

logger = logging.getLogger("HpAgent.SandboxManager")

_LOCAL_SIDE_EFFECT_CLASS = {
    "fs_read": "read_only",
    "Glob": "read_only",
    "Grep": "read_only",
    "fs_write": "idempotent_write",
    "fs_edit": "non_idempotent_write",
    "Bash": "non_idempotent_write",
}


def _declare_local_side_effect(tool: Any, name: str) -> None:
    metadata = dict(getattr(tool, "metadata", {}) or {})
    metadata["side_effect_class"] = _LOCAL_SIDE_EFFECT_CLASS.get(
        name, "unknown"
    )
    tool.metadata = metadata


class SandboxManager:
    """沙箱池管理器 —— 按会话创建 / 查询 / 销毁。

    每个 Sandbox 持有:
      - 一个 ToolRegistry（注册了 workspace 绑定的本地工具 + 共享的 MCP/Skills）
      - 可选的 NsjailExecutor（仅对 Bash 工具加固）
    """

    def __init__(
        self,
        nsjail_config: Optional[NsjailConfig] = None,
        redis_cache: Any = None,
        data_root: Optional[Path] = None,
        max_idle_seconds: int = 300,
        mcp_manager: Any = None,
        skill_definitions: Optional[List[dict]] = None,
        retriever: Any = None,
        native_tools_enabled: bool = True,
        nsjail_enabled: bool = True,
        host_bash_enabled: bool = False,
        file_tools_enabled: bool = False,
        file_output_publisher: Any = None,
        file_conversion_provider: Any = None,
        file_document_router: Any = None,
        work_commands: Any = None,
    ):
        self._nsjail_config = nsjail_config or NsjailConfig()
        self._redis_cache = redis_cache
        self._data_root = Path(data_root).resolve() if data_root else None
        self._max_idle_seconds = max_idle_seconds
        self._mcp_manager = mcp_manager
        self._skill_definitions = skill_definitions or []
        self._retriever = retriever
        self._native_tools_enabled = native_tools_enabled
        self._nsjail_enabled = nsjail_enabled
        # FILE-P0-04: host Bash is an explicit capability, never implied by
        # enabling otherwise-safe native tools.
        self._host_bash_enabled = host_bash_enabled
        self._file_tools_enabled = file_tools_enabled
        self._file_output_publisher = file_output_publisher
        self._file_conversion_provider = file_conversion_provider
        self._file_document_router = file_document_router
        self._work_commands = work_commands

        self._sandboxes: Dict[str, Sandbox] = {}
        self._execution_to_sandbox: Dict[str, str] = {}
        self._run_file_scopes: Dict[str, Any] = {}
        self._execution_active_file_run: Dict[str, str] = {}
        self._lock = RLock()

    def bind_run_file_scope(self, run_id: str, execution_id: str, scope: Any) -> None:
        with self._lock:
            if run_id in self._run_file_scopes or execution_id in self._execution_active_file_run:
                raise RuntimeError("Run file scope is already bound")
            self._run_file_scopes[run_id] = scope
            self._execution_active_file_run[execution_id] = run_id

    def configure_file_document_router(self, router: Any) -> None:
        """Inject the Temporal client after worker composition, before Executions start."""
        with self._lock:
            self._file_document_router = router

    def get_run_file_scope(self, run_id: str) -> Any | None:
        with self._lock:
            return self._run_file_scopes.get(run_id)

    def get_active_run_file_scope(self, execution_id: str) -> Any | None:
        with self._lock:
            run_id = self._execution_active_file_run.get(execution_id)
            return self._run_file_scopes.get(run_id) if run_id else None

    def unbind_run_file_scope(self, run_id: str) -> None:
        with self._lock:
            self._run_file_scopes.pop(run_id, None)
            for execution_id, active_run_id in tuple(self._execution_active_file_run.items()):
                if active_run_id == run_id:
                    del self._execution_active_file_run[execution_id]

    def create_execution_sandbox(
        self,
        execution_id: str,
        workspace_path: str,
        user_uuid: str = "",
        session_context: Optional[dict] = None,
    ) -> str:
        """为 Execution 创建 workspace 绑定的沙箱（幂等——已存在则返回现有 ID）。

        Args:
            execution_id: Execution attempt 资源键。
            workspace_path: 工作区路径。
            user_uuid: 用户 UUID。
            session_context: 会话上下文 dict，包含 account_id、sender_id、
                            channel_type、metadata。供提醒工具等绑定用。
        """
        with self._lock:
            if execution_id in self._execution_to_sandbox:
                return self._execution_to_sandbox[execution_id]

        registry = ToolRegistry()

        ctx = session_context or {"account_id": user_uuid, "channel_type": "work"}
        work_tool_names = ()
        if self._work_commands is not None and ctx.get("source_kind") == "chat":
            from application.main_agent import create_main_work_tools
            tools = create_main_work_tools(ctx, self._work_commands)
            work_tool_names = tuple(tool.name for tool in tools)
            for tool in tools:
                registry.register(tool, category="native", routing=routing_for(tool, "native"))

        if self._native_tools_enabled and not self._file_tools_enabled:
            for name, factory in LOCAL_TOOL_FACTORIES.items():
                if name == "Bash" and not self._host_bash_enabled:
                    continue
                tool = factory(workspace_path)
                _declare_local_side_effect(tool, name)
                registry.register(tool, category="native", routing=routing_for(tool, "native"))
            logger.debug("Session sandbox: %d local tools registered", len(LOCAL_TOOL_FACTORIES))
        else:
            logger.debug("Session sandbox: native tools disabled")

        if self._file_tools_enabled and ctx.get("channel_type") == "web":
            from sandbox.tools.local.file_analysis import create_file_analysis_tools
            from sandbox.tools.local.file_read import create_file_read_tools
            from sandbox.tools.local.file_write import create_file_write_tools
            from sandbox.tools.local.run_candidates import create_run_candidate_tools

            scope_provider = lambda sid=execution_id: self.get_active_run_file_scope(sid)
            for tool in (
                create_run_candidate_tools(scope_provider)
                + create_file_analysis_tools(scope_provider)
                + create_file_read_tools(
                    scope_provider,
                    document_router=self._file_document_router,
                    account_id_provider=lambda value=str(ctx.get("account_id", "")): value,
                )
            ):
                registry.register(tool, category="native", routing=routing_for(tool, "native"))
            if self._file_output_publisher is not None:
                for tool in create_file_write_tools(
                    scope_provider,
                    self._file_output_publisher,
                    self._file_conversion_provider,
                ):
                    if tool.name == "convert_file_to_pdf":
                        continue
                    registry.register(tool, category="native", routing=routing_for(tool, "native"))

        if self._mcp_manager and not self._file_tools_enabled:
            for tool in self._mcp_manager.get_cached_tools():
                registry.register(tool, category="mcp", routing=routing_for(tool, "mcp"))
            logger.debug("Session sandbox: %d MCP tools registered",
                         len(self._mcp_manager.get_cached_tools()))

        if self._skill_definitions and not self._file_tools_enabled:
            from sandbox.tools.skills.engine import build_skill_tool_from_definition
            for skill_def in self._skill_definitions:
                skill_tool = build_skill_tool_from_definition(skill_def, registry)
                registry.register(skill_tool, category="skill", routing=routing_for(skill_tool, "skill"))
            logger.debug("Session sandbox: %d skills registered", len(self._skill_definitions))

        registry.freeze()

        # 首次创建沙箱时同步工具向量库（增量，后续 Execution 跳过已有工具）
        if self._retriever is not None:
            try:
                self._retriever._store.sync(
                    registry.list_all(),
                    embedding_client=self._retriever._embedding,
                )
            except Exception as e:
                import traceback
                logger.warning("Tool vector sync failed: %s", e)
                logger.warning("Tool vector sync traceback:\n%s", traceback.format_exc())

        sandbox_id = str(uuid.uuid4())

        nsjail_executor = None
        if self._nsjail_enabled and self._nsjail_config:
            nsjail_executor = NsjailExecutor(self._nsjail_config)
            logger.debug("Session sandbox: nsjail executor enabled")

        scope_provider = lambda sid=execution_id: self.get_active_run_file_scope(sid)
        context_provider = ToolSelectionContextBuilder(
            surface=str(ctx.get("channel_type") or "unknown"),
            workspace_path=workspace_path, run_scope_provider=scope_provider,
            available_services=(
                frozenset({"gotenberg"})
                if self._file_conversion_provider is not None else frozenset()
            ),
        )
        router = ToolRouter(registry, self._retriever)
        sandbox = Sandbox(
            workspace_path=workspace_path,
            tool_registry=registry,
            sandbox_id=sandbox_id,
            nsjail_executor=nsjail_executor,
            tool_router=router,
            selection_context_provider=context_provider,
        )

        with self._lock:
            self._sandboxes[sandbox_id] = sandbox
            self._execution_to_sandbox[execution_id] = sandbox_id

        tool_counts = registry.count_by_category()
        work_tool_count = sum(
            registry.get_category(name) == "native" for name in work_tool_names
        )
        native_general_count = max(tool_counts["native"] - work_tool_count, 0)
        bash_registered = registry.get_category("Bash") == "native"
        nsjail_binary_ready = bool(
            nsjail_executor
            and os.path.isfile(nsjail_executor.config.nsjail_binary)
        )
        if not bash_registered:
            bash_isolation = "n/a"
        elif nsjail_binary_ready:
            bash_isolation = "nsjail"
        else:
            bash_isolation = "unavailable"

        logger.info(
            "Execution sandbox created: id=%s execution=%s user=%s "
            "tools(total=%d native_general=%d work_tools=%d mcp=%d skills=%d) "
            "bash_registered=%s bash_isolation=%s",
            sandbox_id,
            execution_id,
            user_uuid,
            sum(tool_counts.values()),
            native_general_count,
            work_tool_count,
            tool_counts["mcp"],
            tool_counts["skill"],
            bash_registered,
            bash_isolation,
        )
        return sandbox_id

    def get_sandbox_for_execution(self, execution_id: str) -> Sandbox:
        with self._lock:
            sandbox_id = self._execution_to_sandbox.get(execution_id)
            if not sandbox_id:
                raise SandboxNotFoundError(f"No sandbox for Execution: {execution_id}")
            sandbox = self._sandboxes.get(sandbox_id)
            if not sandbox:
                raise SandboxNotFoundError(sandbox_id)
            return sandbox

    def get_sandbox(self, sandbox_id: str) -> Sandbox:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if not sandbox:
                raise SandboxNotFoundError(sandbox_id)
            return sandbox

    def destroy_sandbox(self, sandbox_id: str) -> bool:
        with self._lock:
            sandbox = self._sandboxes.pop(sandbox_id, None)
            if not sandbox:
                return False
            sandbox.destroy()
            for sid, sbid in list(self._execution_to_sandbox.items()):
                if sbid == sandbox_id:
                    del self._execution_to_sandbox[sid]
            return True

    def list_sandboxes(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [s.get_info() for s in self._sandboxes.values()]

    def get_sandbox_count(self) -> int:
        with self._lock:
            return len(self._sandboxes)

    def cleanup_idle_sandboxes(self) -> int:
        now = time.time()
        with self._lock:
            to_destroy = [
                sid for sid, s in self._sandboxes.items()
                if now - s.last_used > self._max_idle_seconds
            ]
            for sid in to_destroy:
                self._sandboxes[sid].destroy()
                del self._sandboxes[sid]
                for execution_id, sbid in list(self._execution_to_sandbox.items()):
                    if sbid == sid:
                        del self._execution_to_sandbox[execution_id]
            return len(to_destroy)

    def close(self) -> None:
        """Destroy all process-owned sandboxes and clear runtime bindings."""
        with self._lock:
            for sandbox in self._sandboxes.values():
                sandbox.destroy()
            self._sandboxes.clear()
            self._execution_to_sandbox.clear()
            self._run_file_scopes.clear()
            self._execution_active_file_run.clear()
