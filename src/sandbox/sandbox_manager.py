"""
SandboxManager —— 沙箱池管理器，负责多沙箱的生命周期管理。

============================================================================
核心职责
============================================================================

  1. 沙箱创建: create_sandbox() → 创建 NsjailExecutor + Sandbox 并注册到内部池
  2. 沙箱查询: get_sandbox() / list_sandboxes() / get_sandbox_count()
  3. 沙箱销毁: destroy_sandbox() → 调用 sandbox.destroy() 并从池中移除
  4. 空闲回收: cleanup_idle_sandboxes() → 销毁超过 max_idle_seconds 未使用的沙箱
  5. 健康检查: health_check_all() → 批量检查所有沙箱状态

  与旧版的关键区别:
    - 构造函数接受 NsjailConfig（nsjail 全局配置）
    - 构造函数接受 Redis 缓存客户端（用于结果持久化）
    - 构造函数接受 WorkspaceManager（用于 per-session 工作目录）
    - create_sandbox() 自动为每个 Sandbox 创建 NsjailExecutor
    - create_session_sandbox() 为特定会话创建带 workspace 绑载的沙箱

============================================================================
线程安全
============================================================================

  所有对 _sandboxes 字典的读写操作都通过 RLock 保护，支持并发访问。

============================================================================
使用示例
============================================================================

  nsjail_config = NsjailConfig(chroot_path="/sandbox", time_limit=30)
  manager = SandboxManager(
      nsjail_config=nsjail_config,
      redis_cache=redis_cache,
      workspace_manager=workspace_manager,
      max_idle_seconds=300,
  )
  sid = manager.create_sandbox(tools=[calculator_tool])
  # 或为特定会话创建
  sid = manager.create_session_sandbox(
      user_uuid="u1", session_id="s1", tools=[calculator_tool]
  )
  sandbox = manager.get_sandbox(sid)
  result = await sandbox.execute("calculator", {"expression": "1+1"})
  cleaned = manager.cleanup_idle_sandboxes()
"""
from typing import Dict, List, Optional, Any
from threading import RLock
import uuid
import time
import logging
from .sandbox import Sandbox
from .nsjail import NsjailConfig, NsjailExecutor
from .tools.base import BaseTool
from common.errors import SandboxNotFoundError

logger = logging.getLogger("HpAgent.SandboxManager")


class SandboxManager:
    """沙箱池管理器 —— 创建 / 查询 / 销毁 / 空闲回收。

    Attributes:
        _nsjail_config: nsjail 全局配置（所有沙箱共享）。
        _redis_cache: Redis 缓存客户端（None 时不持久化）。
        _workspace_manager: WorkspaceManager 实例（None 时不启用工作区）。
        _sandboxes: sandbox_id → Sandbox 实例的映射。
        _sandbox_meta: sandbox_id → 元数据（user_uuid, session_id）的映射。
        _lock: 可重入锁，保证并发安全。
        _max_idle_seconds: 空闲沙箱的最大存活时间（秒），超时将被清理。
    """

    def __init__(
        self,
        nsjail_config: Optional[NsjailConfig] = None,
        redis_cache: Any = None,
        workspace_manager: Any = None,
        max_idle_seconds: int = 300,
    ):
        """初始化沙箱管理器。

        Args:
            nsjail_config: nsjail 全局配置。None 则使用默认配置。
            redis_cache: RedisCache 实例，用于结果持久化。
            workspace_manager: WorkspaceManager 实例，用于 per-session 工作区。
            max_idle_seconds: 空闲回收阈值，默认 300 秒（5 分钟）。
        """
        self._nsjail_config = nsjail_config or NsjailConfig()
        self._redis_cache = redis_cache
        self._workspace_manager = workspace_manager
        self._sandboxes: Dict[str, Sandbox] = {}
        self._sandbox_meta: Dict[str, dict] = {}
        self._lock = RLock()
        self._max_idle_seconds = max_idle_seconds

    # ── 沙箱生命周期 ──

    def create_sandbox(
        self,
        tools: Optional[List[BaseTool]] = None,
        resources: Optional[Dict[str, Any]] = None,
        sandbox_id: Optional[str] = None,
    ) -> str:
        """创建新沙箱并注册到内部池。

        自动为沙箱创建独立的 NsjailExecutor 实例（共享全局 nsjail 配置
        和 Redis 连接）。

        Args:
            tools: 初始工具列表（BaseTool 元数据）。
            resources: 初始资源字典。
            sandbox_id: 自定义沙箱 ID，None 则自动生成 UUID。

        Returns:
            新创建的 sandbox_id。
        """
        with self._lock:
            executor = NsjailExecutor(self._nsjail_config, self._redis_cache)
            sandbox = Sandbox(
                executor=executor,
                sandbox_id=sandbox_id or str(uuid.uuid4()),
                tools=tools,
                resources=resources,
            )
            self._sandboxes[sandbox.sandbox_id] = sandbox
            return sandbox.sandbox_id

    def create_session_sandbox(
        self,
        user_uuid: str,
        session_id: str,
        tools: Optional[List[BaseTool]] = None,
        resources: Optional[Dict[str, Any]] = None,
    ) -> str:
        """为特定会话创建带工作区绑载的沙箱。

        与 create_sandbox() 的区别:
          - 自动从 WorkspaceManager 获取该会话的 bind mount 参数
          - 将 workspace/ 目录绑载到 nsjail 内部 /work
          - 将 skills/ 目录只读绑载到 nsjail 内部 /skills
          - 记录 user_uuid 和 session_id 用于追踪

        Args:
            user_uuid: 用户 UUID。
            session_id: 会话 ID。
            tools: 初始工具列表。
            resources: 初始资源字典。

        Returns:
            新创建的 sandbox_id。
        """
        sandbox_id = str(uuid.uuid4())

        # 从 WorkspaceManager 获取绑载参数
        extra_bind_mounts: list[str] = []
        work_dir: Optional[str] = None
        if self._workspace_manager:
            extra_bind_mounts = self._workspace_manager.get_nsjail_mounts(user_uuid, session_id)
            # 使用会话 workspace 作为 nsjail 内的工作目录
            work_dir = "/work"

        # 基于全局配置创建 per-session 的 NsjailConfig
        session_config = NsjailConfig(
            nsjail_binary=self._nsjail_config.nsjail_binary,
            chroot_path=self._nsjail_config.chroot_path,
            work_dir=work_dir or self._nsjail_config.work_dir,
            readonly_root=self._nsjail_config.readonly_root,
            python_binary=self._nsjail_config.python_binary,
            runner_script=self._nsjail_config.runner_script,
            user=self._nsjail_config.user,
            group=self._nsjail_config.group,
            hostname=self._nsjail_config.hostname,
            time_limit=self._nsjail_config.time_limit,
            memory_limit_mb=self._nsjail_config.memory_limit_mb,
            cpu_limit_seconds=self._nsjail_config.cpu_limit_seconds,
            max_processes=self._nsjail_config.max_processes,
            max_files=self._nsjail_config.max_files,
            disable_proc=self._nsjail_config.disable_proc,
            disable_network=self._nsjail_config.disable_network,
            bind_mounts=extra_bind_mounts,
            really_quiet=self._nsjail_config.really_quiet,
        )

        with self._lock:
            executor = NsjailExecutor(session_config, self._redis_cache)
            sandbox = Sandbox(
                executor=executor,
                sandbox_id=sandbox_id,
                tools=tools,
                resources=resources,
            )
            self._sandboxes[sandbox_id] = sandbox
            self._sandbox_meta[sandbox_id] = {
                "user_uuid": user_uuid,
                "session_id": session_id,
            }

        logger.info(
            "Session sandbox created: %s (user=%s, session=%s, mounts=%d)",
            sandbox_id, user_uuid, session_id, len(extra_bind_mounts),
        )
        return sandbox_id

    def get_sandbox_meta(self, sandbox_id: str) -> Optional[dict]:
        """获取沙箱关联的 user_uuid 和 session_id。

        Returns:
            {"user_uuid": str, "session_id": str} 或 None。
        """
        with self._lock:
            return self._sandbox_meta.get(sandbox_id)

    def get_sandbox(self, sandbox_id: str) -> Sandbox:
        """按 ID 获取沙箱实例。

        Args:
            sandbox_id: 沙箱唯一标识。

        Returns:
            Sandbox 实例。

        Raises:
            SandboxNotFoundError: 沙箱不存在。
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if not sandbox:
                raise SandboxNotFoundError(sandbox_id)
            return sandbox

    def destroy_sandbox(self, sandbox_id: str) -> bool:
        """销毁指定沙箱并从池中移除。

        Args:
            sandbox_id: 要销毁的沙箱 ID。

        Returns:
            True 表示成功销毁，False 表示沙箱不存在。
        """
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if not sandbox:
                return False
            sandbox.destroy()
            del self._sandboxes[sandbox_id]
            self._sandbox_meta.pop(sandbox_id, None)
            return True

    # ── 查询操作 ──

    def list_sandboxes(self) -> List[Dict[str, Any]]:
        """列出所有沙箱的元信息摘要。"""
        with self._lock:
            return [sandbox.get_info() for sandbox in self._sandboxes.values()]

    def get_sandbox_count(self) -> int:
        """返回当前活跃沙箱数量。"""
        with self._lock:
            return len(self._sandboxes)

    # ── 维护操作 ──

    def cleanup_idle_sandboxes(self) -> int:
        """回收空闲超时的沙箱。

        遍历所有沙箱，将 last_used 距今超过 _max_idle_seconds 的
        沙箱销毁并从池中移除。

        Returns:
            本次清理销毁的沙箱数量。
        """
        with self._lock:
            current_time = time.time()
            to_destroy = []
            for sandbox_id, sandbox in self._sandboxes.items():
                if current_time - sandbox.last_used > self._max_idle_seconds:
                    to_destroy.append(sandbox_id)
            for sandbox_id in to_destroy:
                self._sandboxes[sandbox_id].destroy()
                del self._sandboxes[sandbox_id]
            return len(to_destroy)

    def health_check_all(self) -> Dict[str, bool]:
        """批量健康检查 —— 返回每个沙箱的健康状态映射。"""
        with self._lock:
            return {
                sandbox_id: sandbox.status == "active"
                for sandbox_id, sandbox in self._sandboxes.items()
            }
