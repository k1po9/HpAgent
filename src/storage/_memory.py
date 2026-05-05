"""
内存回退实现 —— 当外部依赖（PostgreSQL / Redis）不可用时的零依赖替代。

这些实现仅供开发和测试使用，生产环境必须使用对应的 PG/Redis 后端。
特点：
  - 零外部依赖（仅标准库）
  - 进程重启即丢失（不持久化）
  - InMemoryPubSub 仅在单进程内有效

选择逻辑在 container.py 的 InfraContainer.build() 中：
  - database_url 为空 → InMemoryKVStore
  - redis_url 为空 → InMemoryPubSub + _NoopCache
"""
from __future__ import annotations

import asyncio
from typing import Any

from .protocols import Handler, KeyValueStore, PubSub, Record, StoreError, StoreErrorCode


class InMemoryKVStore:
    """基于 dict 的 KeyValueStore 实现 —— 进程重启后数据全部丢失。

    与 KeyValueStore 协议保持相同的方法签名，区别是内部使用 dict 存储，
    无任何持久化、索引或事务保证。
    """

    def __init__(self) -> None:
        from datetime import datetime, timezone

        # dict 存储：key → Record
        self._data: dict[str, Record] = {}
        # 时间工厂函数，统一使用 UTC
        self._now = lambda: datetime.now(timezone.utc)

    async def get(self, key: str) -> Record:
        """获取记录。key 不存在时抛出 StoreError(NOT_FOUND)。"""
        if key not in self._data:
            raise StoreError(StoreErrorCode.NOT_FOUND, f"key {key} not found")
        return self._data[key]

    async def set(self, key: str, value: Any) -> None:
        """设置记录。key 已存在时原地更新（保留 created_at，更新 updated_at）。"""
        now = self._now()
        if key in self._data:
            # 更新已存在的记录：覆盖 value 和 updated_at
            self._data[key].value = value
            self._data[key].updated_at = now
        else:
            # 新建记录
            self._data[key] = Record(key=key, value=value, created_at=now, updated_at=now)

    async def delete(self, key: str) -> None:
        """删除记录。key 不存在时静默成功。"""
        self._data.pop(key, None)

    async def list(self, prefix: str | None = None) -> list[Record]:
        """列出记录，可选前缀过滤。按 key 排序返回。"""
        result = list(self._data.values())
        if prefix:
            result = [r for r in result if r.key.startswith(prefix)]
        return sorted(result, key=lambda r: r.key)


class InMemoryPubSub:
    """进程内 Pub/Sub 实现 —— 仅限单进程，不跨实例。

    无网络开销，适合开发调试。多进程部署时不适用。
    """

    def __init__(self) -> None:
        # topic → handler 集合映射
        self._topics: dict[str, set[Handler]] = {}

    async def publish(self, topic: str, payload: bytes) -> None:
        """同步调用所有已注册的 handler（非异步并发 —— 逐个 await）。"""
        for handler in self._topics.get(topic, set()):
            await handler(payload)

    async def subscribe(self, topic: str, handler: Handler) -> None:
        """注册 handler。与 RedisPubSub 接口一致。"""
        self._topics.setdefault(topic, set()).add(handler)

    async def unsubscribe(self, topic: str, handler: Handler) -> None:
        """取消注册。当 topic 无剩余 handler 时清理该 topic 条目。"""
        if topic in self._topics:
            self._topics[topic].discard(handler)
            if not self._topics[topic]:
                del self._topics[topic]
