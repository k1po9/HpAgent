# HpAgent v5 存储基础设施层

> 基于 `docs/version/v5/durable.md` 设计，提供生产可用的多后端基础存储能力。
> 完全替代旧有 `src/session/repositories.py` 中的混杂实现。

---

## 1. 分层架构

```
┌───────────────────────────────────┐
│   记忆层 (src/session/)           │  ← 先留空，仅保留 TemporalSessionManager + 数据模型
├───────────────────────────────────┤
│   存储抽象层 (storage/protocols)   │  ← typing.Protocol 统一接口
├───────────────┬─────────┬─────────┤
│ 文件系统实现  │ PG 实现 │ Redis   │
│ (AioFileStore)│ (SQLA)  │ (Pub)   │
└───────────────┴─────────┴─────────┘
```

所有上层代码仅通过 `typing.Protocol` 操作存储，不直接依赖具体实现。

---

## 2. 文件清单

| 文件 | 职责 |
|------|------|
| `src/storage/__init__.py` | 公开 API 导出（协议、File 实现、DI 容器），不含重依赖 |
| `src/storage/protocols.py` | `Record`, `KeyValueStore`, `FileStore`, `PubSub` Protocol 定义 + `StoreError` 错误体系 |
| `src/storage/file.py` | `AioFileStore` — 通用文件存储，原子写入，路径沙箱 |
| `src/storage/postgres.py` | `SqlKeyValueStore` + 8 张 Agent 记忆表 DDL + 引擎/会话工厂 |
| `src/storage/redis.py` | `RedisCache` (带 TTL) + `RedisPubSub` (发布/订阅) |
| `src/storage/_memory.py` | `InMemoryKVStore` + `InMemoryPubSub` — 无外部依赖的开发回退 |
| `src/storage/container.py` | `AppConfig` + `InfraContainer` — 启动时依赖注入组装 |

---

## 3. 核心协议

所有后端实现遵循同一组协议，上层代码只依赖协议而非具体类。

### 3.1 KeyValueStore

```python
class KeyValueStore(Protocol):
    async def get(self, key: str) -> Record: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def list(self, prefix: str | None = None) -> list[Record]: ...
```

### 3.2 FileStore

```python
class FileStore(Protocol):
    async def read(self, path: str) -> str: ...
    async def write(self, path: str, content: str) -> None: ...
    async def delete(self, path: str) -> None: ...
    async def list(self, directory: str, pattern: str = "*") -> list[str]: ...
```

### 3.3 PubSub

```python
class PubSub(Protocol):
    async def publish(self, topic: str, payload: bytes) -> None: ...
    async def subscribe(self, topic: str, handler: Handler) -> None: ...
    async def unsubscribe(self, topic: str, handler: Handler) -> None: ...
```

### 3.4 数据类型

```python
@dataclass
class Record:
    key: str
    value: Any          # JSON 可序列化
    created_at: datetime
    updated_at: datetime
```

**设计要点：**
- 所有方法均为 `async`，适应 IO 密集型场景
- 错误统一使用 `StoreError`，绝不返回 `None` 或泄漏驱动异常
- 文件存储使用 POSIX 风格路径，不假定任何文件格式

---

## 4. 错误体系

所有实现将底层异常转换为统一的 `StoreError`：

| 错误码 | 触发条件 |
|--------|---------|
| `NOT_FOUND` | 键/文件不存在 |
| `DUPLICATE` | 唯一约束冲突 |
| `CONNECTION_FAILED` | 数据库/Redis 连接失败 |
| `PERMISSION_DENIED` | 路径逃逸沙箱根目录 |
| `INVALID_DATA` | 外键/非空约束违反 |

```python
class StoreError(Exception):
    def __init__(self, code: StoreErrorCode, message: str, original: Exception | None = None):
        self.code = code
        self.message = message
        self.original = original

def normalize_db_error(err: Exception, entity: str, operation: str) -> StoreError:
    """将 asyncpg 驱动异常映射为 StoreError"""
```

---

## 5. 后端实现

### 5.1 AioFileStore（文件系统）

- 基于 `aiofiles`，依赖惰性导入
- **原子写入**：先写 `.tmp` 临时文件，再 `os.replace` 重命名
- **路径沙箱**：`_resolve()` 拒绝 root 之外的路径，防止目录遍历
- 通用读写字符串，上层自行决定格式（Markdown / JSON / YAML）

```python
store = AioFileStore(root=Path("data/memory"))
await store.write("sessions/abc.json", json_str)
content = await store.read("sessions/abc.json")
files = await store.list("sessions", pattern="*.json")
```

### 5.2 SqlKeyValueStore（PostgreSQL）

- 基于 SQLAlchemy Core + asyncpg
- 使用 `kv_store` 通用键值表
- Upsert 语义：`ON CONFLICT DO UPDATE`
- Session factory 模式管理事务边界

```python
engine = create_engine("postgresql+asyncpg://user:pass@localhost/db")
await ensure_schema(engine)
sf = create_session_factory(engine)
kv = SqlKeyValueStore(sf)
await kv.set("config:theme", "dark")
record = await kv.get("config:theme")
```

### 5.3 Agent 记忆专用表（8 张）

完整 DDL 定义在 `postgres.py`，`ensure_schema()` 自动创建：

| 表名 | 用途 | 关键字段 |
|------|------|---------|
| `users` | 用户主表 | id(UUID), nickname, status, last_active_at |
| `user_identities` | 多平台身份关联 | user_id, platform, identifier, UNIQUE(platform, identifier) |
| `phone_verifications` | 手机验证审计 | phone, code_hash(SHA-256), attempts, expires_at |
| `sessions` | 会话表 | id, user_id, platform, status, message_count |
| `messages` | 消息流水 | id, session_id, role, content, metadata(JSONB), token_count |
| `memory_events` | 记忆事件 | user_id, event_type, subject, predicate, object(JSONB), confidence |
| `user_profiles` | 用户画像 | user_id(PK), preferences, knowledge_tags, behavioral_summary, custom_context |
| `knowledge_files` | 知识文件索引 | file_path(UNIQUE), category, owner_user_id, checksum |

**表关系：**
```
users (1) ──< user_identities (N)
users (1) ──< sessions (N)
sessions (1) ──< messages (N)
users (1) ──< memory_events (N)
users (1) ──  user_profiles (1)
users (0,1) ─< knowledge_files (N)
```

**设计原则：**
- 全局唯一 UUID 主键（建议 v7 时间有序）
- `users` 与 `user_identities` 解耦，同一用户可绑定 QQ/手机/Web 多平台
- 敏感数据最小存储：验证码明文仅存 Redis，数据库仅存 hash
- JSONB 灵活属性：画像、记忆内容可动态扩展
- 软删除：`status` 字段替代物理删除
- 预留 `pgvector` 向量字段，启用后零影响现有表

### 5.4 RedisCache + RedisPubSub

```python
# 缓存（带 TTL）
cache = RedisCache(redis, default_ttl=300)
await cache.set(b"key", b"value", ttl=60)
data = await cache.get(b"key")

# 发布/订阅（每 topic 一个后台监听任务）
pubsub = RedisPubSub(redis)
await pubsub.subscribe("user:123", handler)
await pubsub.publish("user:123", payload)
```

### 5.5 开发回退（InMemoryKVStore / InMemoryPubSub）

当 `database_url` 或 `redis_url` 为空时自动启用：
- `InMemoryKVStore` — 纯 dict 存储，进程重启丢失
- `InMemoryPubSub` — 单进程内 Pub/Sub
- `_NoopCache` — 所有 get 返回 None

---

## 6. 依赖注入与启动组装

```python
from storage import AppConfig, InfraContainer

config = AppConfig(
    memory_dir=Path("data/memory"),
    database_url="postgresql+asyncpg://user:pass@localhost/db",  # 空则用 InMemory
    redis_url="redis://localhost:6379",                           # 空则用 InMemory
)
infra = await InfraContainer.build(config)

# 使用
await infra.file_store.write("path/file.md", content)
await infra.kv_store.set("key", value)
await infra.pubsub.publish("topic", payload)
data = await infra.redis_cache.get("key")
```

---

## 7. 与旧实现的对比

| 维度 | 旧 (session/repositories.py) | 新 (storage/) |
|------|---------------------------|--------------|
| 文件位置 | 混在 session/ 领域层 | 独立 storage/ 基础设施层 |
| 抽象方式 | 具体类直接依赖 | typing.Protocol 接口 |
| 错误处理 | 返回 None / 原始异常 | 统一 StoreError + StoreErrorCode |
| 账号存储 | PostgresAccountRepository 放在 session 下 | 归属 PG 后端，与领域解耦 |
| 文件存储 | 同步 JSON 读写，无原子性 | 异步，原子写入，路径沙箱 |
| 表结构 | 运行时创建，无索引 | 完整 DDL + 12 个索引，ensure_schema() |
| 无依赖模式 | 不支持 | InMemoryKVStore / InMemoryPubSub 自动回退 |
| 依赖注入 | 手动 new | InfraContainer.build() 统一组装 |

---

## 8. 待完成（记忆层重建）

当前 `src/session/` 仅保留：
- `Session`, `EventRecord`, `SessionStatus` 数据模型
- `TemporalSessionManager`（通过 Temporal Workflow Queries 读事件，不依赖本地存储）

后续记忆层需基于 `storage.InfraContainer` 重建：
1. 实现新的 `SessionManager`，通过 `infra.kv_store` / `infra.file_store` 持久化
2. 会话事件写入 `messages` 表，记忆提取写入 `memory_events` 表
3. 跨会话用户画像聚合写入 `user_profiles` 表
4. 知识文件索引入 `knowledge_files` 表
