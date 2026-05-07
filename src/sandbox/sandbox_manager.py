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
    - create_sandbox() 自动为每个 Sandbox 创建 NsjailExecutor

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
      max_idle_seconds=300,
  )
  sid = manager.create_sandbox(tools=[calculator_tool])
  sandbox = manager.get_sandbox(sid)
  result = await sandbox.execute("calculator", {"expression": "1+1"})
  cleaned = manager.cleanup_idle_sandboxes()
"""
from typing import Dict, List, Optional, Any
from threading import RLock
import uuid
import time
from .sandbox import Sandbox
from .nsjail import NsjailConfig, NsjailExecutor
from .tools.base import BaseTool
from common.errors import SandboxNotFoundError


class SandboxManager:
    """沙箱池管理器 —— 创建 / 查询 / 销毁 / 空闲回收。

    Attributes:
        _nsjail_config: nsjail 全局配置（所有沙箱共享）。
        _redis_cache: Redis 缓存客户端（None 时不持久化）。
        _sandboxes: sandbox_id → Sandbox 实例的映射。
        _lock: 可重入锁，保证并发安全。
        _max_idle_seconds: 空闲沙箱的最大存活时间（秒），超时将被清理。
    """

    def __init__(
        self,
        nsjail_config: Optional[NsjailConfig] = None,
        redis_cache: Any = None,
        max_idle_seconds: int = 300,
    ):
        """初始化沙箱管理器。

        Args:
            nsjail_config: nsjail 全局配置。None 则使用默认配置。
            redis_cache: RedisCache 实例，用于结果持久化。
            max_idle_seconds: 空闲回收阈值，默认 300 秒（5 分钟）。
        """
        self._nsjail_config = nsjail_config or NsjailConfig()
        self._redis_cache = redis_cache
        self._sandboxes: Dict[str, Sandbox] = {}
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
