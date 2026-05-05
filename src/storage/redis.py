"""
Redis 集成 —— 提供缓存和发布/订阅两种能力。

  1. RedisCache：带默认 TTL 的字符串缓存。附加 get_json / set_json 便捷方法。
  2. RedisPubSub：基于 Redis Pub/Sub 的发布/订阅实现，每 topic 一个后台监听任务。

Redis 客户端由调用方通过构造函数注入（不在此模块内创建连接），
使得连接配置和生命周期管理完全由 container.py 控制。

依赖：pip install redis

当 redis_url 为空时，container.py 自动回退到 InMemoryPubSub + _NoopCache，
因此本模块仅在需要 Redis 时导入。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .protocols import Handler

logger = logging.getLogger(__name__)


class RedisCache:
    """基于 Redis 字符串类型的缓存封装。

    使用 Redis SET 命令的 EX 参数实现 TTL 过期。
    附加 get_json / set_json 方法处理 JSON 序列化。

    用法示例::

        cache = RedisCache(redis_client, default_ttl=300)
        await cache.set(b"session:abc123", json_bytes)
        data = await cache.get(b"session:abc123")
        # JSON 快捷方法
        await cache.set_json("user:123", {"name": "nono"}, ttl=600)
        user = await cache.get_json("user:123")
    """

    def __init__(self, redis, default_ttl: int = 300) -> None:
        """
        Args:
            redis: redis.asyncio.Redis 客户端实例（已连接）。
            default_ttl: 默认过期时间（秒），当 set 时未指定 ttl 时使用。默认 300 秒（5 分钟）。
        """
        self.redis = redis
        self.ttl = default_ttl

    async def get(self, key: str) -> bytes | None:
        """获取原始字节值。key 不存在时返回 None（与 Redis GET 行为一致）。"""
        return await self.redis.get(key)

    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        """设置字节值，可选 TTL。

        Args:
            key: 缓存键。
            value: 原始字节值。
            ttl: 过期时间（秒）。None 时使用 default_ttl。
        """
        await self.redis.set(key, value, ex=ttl or self.ttl)

    async def delete(self, key: str) -> None:
        """删除缓存键（幂等 —— 键不存在不报错）。"""
        await self.redis.delete(key)

    async def get_json(self, key: str) -> Any:
        """获取 JSON 反序列化后的值。key 不存在时返回 None。"""
        import json

        raw = await self.redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        """将 Python 对象 JSON 序列化后写入缓存。

        Args:
            key: 缓存键。
            value: 任意 JSON 可序列化的 Python 对象。
            ttl: 过期时间（秒）。None 时使用 default_ttl。
        """
        import json

        await self.redis.set(key, json.dumps(value).encode(), ex=ttl or self.ttl)


class RedisPubSub:
    """基于 Redis Pub/Sub 通道的发布/订阅实现。

    实现 PubSub 协议。内部按 topic 维护 handler 集合和后台监听任务。
    当某 topic 首次被订阅时，创建 asyncio.Task 持续监听；当该 topic
    所有 handler 取消订阅后，停止并清理监听任务。

    通道命名：所有 topic 自动添加 "app:" 前缀，
    如 subscribe("user:123") → 实际订阅 Redis channel "app:user:123"。

    用法示例::

        pubsub = RedisPubSub(redis_client)
        await pubsub.subscribe("session:abc", my_handler)
        await pubsub.publish("session:abc", json.dumps(event).encode())
    """

    def __init__(self, redis) -> None:
        """
        Args:
            redis: redis.asyncio.Redis 客户端实例（已连接）。
        """
        self.redis = redis
        # topic → handler 集合的映射，一个 topic 下可以有多个 handler（广播语义）
        self._handlers: dict[str, set[Handler]] = {}
        # topic → 后台监听 Task 的映射，每个 topic 只有一个监听任务
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def publish(self, topic: str, payload: bytes) -> None:
        """向指定 topic 发布消息。

        Args:
            topic: 主题名（不含前缀，内部自动加 "app:"）。
            payload: 二进制载荷。
        """
        await self.redis.publish(f"app:{topic}", payload)

    async def subscribe(self, topic: str, handler: Handler) -> None:
        """订阅 topic，当消息到达时回调 handler。

        同一 handler 重复订阅同一 topic 是幂等的（set 去重）。
        首次订阅该 topic 时启动后台 asyncio.Task 监听。

        Args:
            topic: 订阅的主题名。
            handler: 异步回调函数，签名为 async def handler(payload: bytes) -> None。
        """
        self._handlers.setdefault(topic, set()).add(handler)
        # 只有第一个 handler 注册时才启动监听任务
        if len(self._handlers[topic]) == 1:
            self._tasks[topic] = asyncio.create_task(self._listen(topic))

    async def unsubscribe(self, topic: str, handler: Handler) -> None:
        """取消订阅。当 topic 下无剩余 handler 时，取消并移除后台监听任务。

        Args:
            topic: 取消订阅的主题名。
            handler: 要移除的 handler 引用（必须与 subscribe 时传入的相同）。
        """
        if topic not in self._handlers:
            return
        self._handlers[topic].discard(handler)
        if not self._handlers[topic]:
            # 该 topic 下已无 handler —— 清理资源
            del self._handlers[topic]
            task = self._tasks.pop(topic, None)
            if task:
                task.cancel()

    async def _listen(self, topic: str) -> None:
        """后台监听协程 —— 每个 topic 一个实例。

        流程：
          1. 创建独立的 pubsub 连接（不共享主连接）
          2. SUBSCRIBE 到 "app:{topic}" 通道
          3. 循环 listen() 接收消息
          4. 每收到一条 message，分发给该 topic 下所有注册的 handler
          5. handler 抛异常时记录日志但不影响其他 handler 和监听循环
          6. 任务被 cancel 时正常退出，unsubscribe 通道
        """
        pubsub = self.redis.pubsub()
        channel = f"app:{topic}"
        await pubsub.subscribe(channel)
        try:
            async for msg in pubsub.listen():
                # Redis pubsub 消息格式: {type, channel, data}
                if msg["type"] != "message":
                    continue
                data = msg["data"]
                # 分发给该 topic 下的所有 handler（广播）
                for handler in self._handlers.get(topic, set()):
                    try:
                        await handler(data)
                    except Exception:
                        # 单个 handler 失败不影响其他 handler
                        logger.exception("PubSub handler error for topic %s", topic)
        except asyncio.CancelledError:
            # 正常的取消流程 —— 静默退出
            pass
        finally:
            # 无论何种退出路径，确保取消订阅释放 Redis 端连接
            await pubsub.unsubscribe(channel)
