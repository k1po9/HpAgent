"""
依赖注入容器 —— 在启动时按配置组装所有存储后端。

组装逻辑（InfraContainer.build()）：
  1. 始终创建 AioFileStore（文件存储只需磁盘，无外部依赖）。
  2. 如果 database_url 非空 → 连接 PostgreSQL，创建 SqlKeyValueStore。
     否则 → 使用 InMemoryKVStore（开发回退）。
  3. 如果 redis_url 非空 → 连接 Redis，创建 RedisPubSub + RedisCache。
     否则 → 使用 InMemoryPubSub + _NoopCache（开发回退）。

所有后端实例统一挂载在 InfraContainer 上，上层通过 container.kv_store /
container.file_store 等属性获取，不关心底层是 PG 还是内存。

用法示例::

    from storage import AppConfig, InfraContainer

    config = AppConfig(
        memory_dir=Path("data/memory"),
        database_url="postgresql+asyncpg://user:pass@localhost/db",  # 空字符串 → 内存回退
        redis_url="redis://localhost:6379",                          # 空字符串 → 内存回退
    )
    infra = await InfraContainer.build(config)

    # 使用存储
    await infra.file_store.write("path/file.md", content)
    await infra.kv_store.set("key", value)
    await infra.pubsub.publish("topic", payload)
    data = await infra.redis_cache.get("key")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .file import AioFileStore
from .protocols import FileStore, KeyValueStore, PubSub

logger = logging.getLogger(__name__)


@dataclass
class AppConfig:
    """应用级存储配置 —— 由启动脚本从环境变量/YAML 配置文件中加载后填充。

    Attributes:
        memory_dir: 文件存储的根目录，默认 "data/memory"（项目根目录相对路径）。
        database_url: PostgreSQL 连接串。空字符串表示不使用 PG，自动回退到 InMemoryKVStore。
        redis_url: Redis 连接串。空字符串表示不使用 Redis，自动回退到 InMemoryPubSub + _NoopCache。
    """

    memory_dir: Path = field(default_factory=lambda: Path("data/memory"))
    database_url: str = ""
    redis_url: str = ""


@dataclass
class InfraContainer:
    """存储基础设施容器 —— 持有所有后端实例。

    这是整个存储层的"组装点"。上层代码通过此容器获取存储实例，
    不关心底层是 PostgreSQL 还是内存 dict。

    Attributes:
        file_store: 文件存储实例（始终为 AioFileStore，因为只需磁盘）。
        kv_store: 键值存储实例（PG SqlKeyValueStore 或 InMemoryKVStore）。
        pubsub: 发布/订阅实例（RedisPubSub 或 InMemoryPubSub）。
        redis_cache: Redis 缓存实例（RedisCache 或 _NoopCache）。
        config: 当前使用的配置对象，便于运行时读取。
        pg_engine: SQLAlchemy AsyncEngine（仅 PG 模式非空）。
        pg_session_factory: async_sessionmaker（仅 PG 模式非空）。
        pg_metadata: SQLAlchemy MetaData（仅 PG 模式非空）。
    """

    file_store: FileStore
    kv_store: KeyValueStore
    pubsub: PubSub
    redis_cache: Any            # RedisCache | _NoopCache
    config: AppConfig

    # PG 引擎级对象 —— 上层如需执行原生 SQL 可通过这些直接操作
    pg_engine: Any = None
    pg_session_factory: Any = None
    pg_metadata: Any = None

    @classmethod
    async def build(cls, config: AppConfig) -> "InfraContainer":
        """根据 AppConfig 组装所有存储后端。

        这是唯一的入口点。所有后端选择和初始化逻辑集中在此方法中，
        避免分散到各模块的 __init__。
        """
        # ── 文件存储（始终启用，无外部依赖） ──────────────────────────
        file_store = AioFileStore(root=config.memory_dir)
        logger.info("File store root: %s", config.memory_dir)

        # ── PostgreSQL（按需启用） ─────────────────────────────────────
        pg_engine = None
        pg_session_factory = None
        pg_metadata = None
        kv_store: KeyValueStore

        if config.database_url:
            # 仅在需要时延迟导入 PostgreSQL 模块（避免未安装 sqlalchemy 时崩溃）
            from .postgres import (
                SqlKeyValueStore,
                create_engine,
                create_session_factory,
                ensure_schema,
                metadata,
            )
            pg_engine = create_engine(config.database_url)
            await ensure_schema(pg_engine)                 # 幂等建表建索引
            pg_session_factory = create_session_factory(pg_engine)
            kv_store = SqlKeyValueStore(pg_session_factory)
            pg_metadata = metadata
            logger.info("PostgreSQL connected, schema ensured")
        else:
            # 未配置 database_url → 使用内存回退
            from ._memory import InMemoryKVStore
            kv_store = InMemoryKVStore()
            logger.info("No database_url — using in-memory KeyValueStore")

        # ── Redis（按需启用） ──────────────────────────────────────────
        pubsub: PubSub
        redis_cache: Any

        if config.redis_url:
            # 仅在需要时延迟导入 Redis 模块
            from .redis import RedisCache, RedisPubSub
            import redis.asyncio as aioredis

            redis = aioredis.from_url(config.redis_url, decode_responses=False)
            pubsub = RedisPubSub(redis)
            redis_cache = RedisCache(redis)
            logger.info("Redis connected: %s", config.redis_url)
        else:
            # 未配置 redis_url → 使用内存回退
            from ._memory import InMemoryPubSub

            pubsub = InMemoryPubSub()
            redis_cache = _NoopCache()
            logger.info("No redis_url — using in-memory PubSub")

        return cls(
            file_store=file_store,
            kv_store=kv_store,
            pubsub=pubsub,
            redis_cache=redis_cache,
            config=config,
            pg_engine=pg_engine,
            pg_session_factory=pg_session_factory,
            pg_metadata=pg_metadata,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# 开发回退
# ═══════════════════════════════════════════════════════════════════════════════


class _NoopCache:
    """空操作缓存 —— 所有 get 返回 None，set/delete 不做任何事。

    用于未配置 Redis 但又需要 redis_cache 属性的场景。
    上层代码无需判空，直接调用即可（get 永远返回 None）。
    """

    ttl: int = 0

    async def get(self, key: str) -> None:
        return None

    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        pass

    async def delete(self, key: str) -> None:
        pass

    async def get_json(self, key: str) -> None:
        return None

    async def set_json(self, key: str, value: Any, ttl: int | None = None) -> None:
        pass
