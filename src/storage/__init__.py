"""
存储基础设施层 —— 多后端持久化抽象。

后端类型：
  - File：AioFileStore（基于 aiofiles 的异步文件存储，原子写入 + 路径沙箱）
  - PostgreSQL：SqlKeyValueStore + 8 张 Agent 记忆专用表（SQLAlchemy Core + asyncpg）
  - Redis：RedisCache（带 TTL 缓存）+ RedisPubSub（跨进程发布/订阅）

所有上层代码依赖 typing.Protocol 接口，绝不直接使用具体实现类。
切换后端只需修改 AppConfig 配置，业务代码零改动。

重依赖模块（postgres / redis）采用惰性导入：
  - 核心协议和 File 实现在 import storage 时立即可用
  - PG / Redis 模块仅在 AppConfig 中配置了对应 URL 时才加载
  - 未配置时自动使用 InMemoryKVStore / InMemoryPubSub 回退

用法::

    from storage import AppConfig, InfraContainer

    config = AppConfig(database_url="postgresql+asyncpg://...", redis_url="redis://...")
    infra = await InfraContainer.build(config)

    await infra.file_store.write("sessions/abc.json", data)
    await infra.kv_store.set("config:theme", "dark")
    await infra.pubsub.publish("session:created", payload)

直接使用重依赖模块（需确保已安装对应包）::

    from storage.postgres import SqlKeyValueStore, ensure_schema
    from storage.redis import RedisCache, RedisPubSub
"""
from ._memory import InMemoryKVStore, InMemoryPubSub
from .container import AppConfig, InfraContainer
from .file import AioFileStore
from .protocols import (
    FileStore,
    Handler,
    KeyValueStore,
    PubSub,
    Record,
    StoreError,
    StoreErrorCode,
    normalize_db_error,
)

__all__ = [
    # ── 协议与类型 ──
    "KeyValueStore",
    "FileStore",
    "PubSub",
    "Record",
    "Handler",
    "StoreError",
    "StoreErrorCode",
    "normalize_db_error",
    # ── 文件存储 ──
    "AioFileStore",
    # ── 内存回退 ──
    "InMemoryKVStore",
    "InMemoryPubSub",
    # ── 依赖注入 ──
    "AppConfig",
    "InfraContainer",
]
