"""执行上下文 — 分层状态管理，具备并发安全性。

设计决策（来自五轮架构评审）：
    - 将 SessionState、SharedMemory 和 RuntimeConfig 分离（而非单一的 global_context dict）
    - SharedMemory 提供 CAS 原语以防止竞态条件
    - 使用 trace_id 以实现全链路可观测性
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    """用户会话 — 会话历史、用户身份。"""
    user_id: str = ""
    conversation_history: list = field(default_factory=list)


class SharedMemory:
    """在代理间共享的并发安全键值存储。

    支持命名空间隔离和原子 CAS 操作。
    在 asyncio 的单线程模型中，dict 的写入操作是线程安全的，但存在读-检查-写的逻辑竞态，
    需要使用 CAS 来避免此类竞态。
    """

    def __init__(self) -> None:
        self._namespaces: dict[str, dict] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, namespace: str, key: str) -> Any:
        async with self._lock:
            return self._namespaces.get(namespace, {}).get(key)

    async def set(self, namespace: str, key: str, value: Any) -> None:
        async with self._lock:
            if namespace not in self._namespaces:
                self._namespaces[namespace] = {}
            self._namespaces[namespace][key] = value

    async def compare_and_set(self, namespace: str, key: str, expected: Any, new_value: Any) -> bool:
        """原子 CAS —— 防止竞态写入。"""
        async with self._lock:
            current = self._namespaces.get(namespace, {}).get(key)
            if current == expected:
                if namespace not in self._namespaces:
                    self._namespaces[namespace] = {}
                self._namespaces[namespace][key] = new_value
                return True
            return False

    async def delete(self, namespace: str, key: str) -> bool:
        async with self._lock:
            ns = self._namespaces.get(namespace)
            if ns and key in ns:
                del ns[key]
                return True
            return False

    def snapshot(self) -> dict[str, dict]:
        """返回所有命名空间的浅拷贝（未在锁下进行——调用方需确保并发安全）。"""
        return {ns: dict(items) for ns, items in self._namespaces.items()}


@dataclass
class RuntimeConfig:
    """运行时配置 —— 超时、重试、模型选择。"""
    timeout_seconds: int = 300
    max_retries: int = 3
    model_name: str = "default"


@dataclass
class ExecutionContext:
    """分层执行上下文，贯穿整个编排流程。"""
    session: SessionState = field(default_factory=SessionState)
    shared_memory: SharedMemory = field(default_factory=SharedMemory)
    config: RuntimeConfig = field(default_factory=RuntimeConfig)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
