"""
SandboxManager —— 沙箱池管理器，按会话创建 workspace 绑定的沙箱。

核心职责:
  1. 创建: create_session_sandbox() → 用 LOCAL_TOOL_FACTORIES 创建 workspace 绑定的本地工具
  2. 查询: get_sandbox_for_session() / get_sandbox()
  3. 销毁: destroy_sandbox()
  4. 空闲回收: cleanup_idle_sandboxes()

每个会话一个 Sandbox，会话结束时销毁。
"""
from typing import Dict, List, Optional, Any
from threading import RLock
from pathlib import Path
import uuid
import time
import logging

from .sandbox import Sandbox
from .nsjail import NsjailConfig, NsjailExecutor
from sandbox.tools.registry import ToolRegistry
from sandbox.tools.local import LOCAL_TOOL_FACTORIES
from common.errors import SandboxNotFoundError

logger = logging.getLogger("HpAgent.SandboxManager")


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
        max_merged_multiplier: float = 1.5,
        per_query_min: int = 3,
    ):
        self._nsjail_config = nsjail_config or NsjailConfig()
        self._redis_cache = redis_cache
        self._data_root = Path(data_root).resolve() if data_root else None
        self._max_idle_seconds = max_idle_seconds
        self._mcp_manager = mcp_manager
        self._skill_definitions = skill_definitions or []
        self._retriever = retriever
        self._max_merged_multiplier = max_merged_multiplier
        self._per_query_min = per_query_min

        self._sandboxes: Dict[str, Sandbox] = {}
        self._session_to_sandbox: Dict[str, str] = {}
        self._lock = RLock()

    def create_session_sandbox(
        self,
        session_id: str,
        workspace_path: str,
        user_uuid: str = "",
    ) -> str:
        """为会话创建 workspace 绑定的沙箱（幂等——已存在则返回现有 ID）。"""
        with self._lock:
            if session_id in self._session_to_sandbox:
                return self._session_to_sandbox[session_id]

        registry = ToolRegistry(retriever=self._retriever, per_query_min=self._per_query_min)

        for name, factory in LOCAL_TOOL_FACTORIES.items():
            tool = factory(workspace_path)
            registry.register(tool, category="native")
        logger.debug("Session sandbox: %d local tools registered", len(LOCAL_TOOL_FACTORIES))

        if self._mcp_manager:
            for tool in self._mcp_manager.get_cached_tools():
                registry.register(tool, category="mcp")
            logger.debug("Session sandbox: %d MCP tools registered",
                         len(self._mcp_manager.get_cached_tools()))

        if self._skill_definitions:
            from sandbox.tools.skills.engine import build_skill_tool_from_definition
            for skill_def in self._skill_definitions:
                skill_tool = build_skill_tool_from_definition(skill_def, registry)
                registry.register(skill_tool, category="skill")
            logger.debug("Session sandbox: %d skills registered", len(self._skill_definitions))

        registry.freeze()

        # 首次创建沙箱时同步工具向量库（增量，后续 session 跳过已有工具）
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
        sandbox = Sandbox(
            workspace_path=workspace_path,
            tool_registry=registry,
            sandbox_id=sandbox_id,
            nsjail_executor=None,
            max_merged_multiplier=self._max_merged_multiplier,
        )

        with self._lock:
            self._sandboxes[sandbox_id] = sandbox
            self._session_to_sandbox[session_id] = sandbox_id

        logger.info(
            "Session sandbox created: %s (session=%s, user=%s, native=%d)",
            sandbox_id, session_id, user_uuid, len(LOCAL_TOOL_FACTORIES),
        )
        return sandbox_id

    def get_sandbox_for_session(self, session_id: str) -> Sandbox:
        with self._lock:
            sandbox_id = self._session_to_sandbox.get(session_id)
            if not sandbox_id:
                raise SandboxNotFoundError(f"No sandbox for session: {session_id}")
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
            for sid, sbid in list(self._session_to_sandbox.items()):
                if sbid == sandbox_id:
                    del self._session_to_sandbox[sid]
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
                for session_id, sbid in list(self._session_to_sandbox.items()):
                    if sbid == sid:
                        del self._session_to_sandbox[session_id]
            return len(to_destroy)
