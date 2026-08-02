# Python 编码智能体——存储基础设施层参考

> **目标**：提供生产可用的多后端基础存储能力，包含通用持久化抽象与Agent记忆系统专用数据库实现。  
> **覆盖**：文件系统（MD 文件）、PostgreSQL（SQLAlchemy/asyncpg）、Redis（缓存 & Pub/Sub）的统一抽象与实现。

---

## 1. 整体分层

```
┌───────────────────────────────────┐
│   上层（先不涉及）              │  ← 记忆层、业务逻辑、Prompt 组装等
├───────────────────────────────────┤
│   存储抽象层（Protocols）          │  ← 统一的存储接口
├───────────────┬─────────┬─────────┤
│ 文件系统实现  │ PG 实现 │ Redis   │
│ (md 文件)     │ (SQLA)  │ (Pub)   │
└───────────────┴─────────┴─────────┘
```

所有上层代码只通过 **`typing.Protocol`** 操作存储，不直接依赖具体实现。

---

## 2. 核心存储协议

不假定任何业务实体，提供最基础的 CRUD 和文件操作协议：

```python
# stores/protocols.py
from typing import Protocol, runtime_checkable, Any
from dataclasses import dataclass
from datetime import datetime

# ---------- 通用键值/文档协议 ----------
@dataclass
class Record:
    key: str
    value: Any               # JSON 可序列化
    created_at: datetime
    updated_at: datetime

class KeyValueStore(Protocol):
    async def get(self, key: str) -> Record: ...
    async def set(self, key: str, value: Any) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def list(self, prefix: str | None = None) -> list[Record]: ...

# ---------- 文件存储协议 ----------
class FileStore(Protocol):
    async def read(self, path: str) -> str: ...
    async def write(self, path: str, content: str) -> None: ...
    async def delete(self, path: str) -> None: ...
    async def list(self, directory: str, pattern: str = "*") -> list[str]: ...

# ---------- 发布/订阅协议 ----------
from typing import Callable, Awaitable
type Handler = Callable[[bytes], Awaitable[None]]

class PubSub(Protocol):
    async def publish(self, topic: str, payload: bytes) -> None: ...
    async def subscribe(self, topic: str, handler: Handler) -> None: ...
    async def unsubscribe(self, topic: str, handler: Handler) -> None: ...
```

> **设计要点**：  
> - 所有方法均为 `async`，适应 IO 密集型场景。  
> - 错误统一使用 `StoreError`（见下一节），绝不返回 `None` 或泄漏驱动异常。  
> - 文件存储使用 POSIX 风格路径，不假定任何文件格式（如 Markdown），通用读/写字符串。

---

## 3. 文件系统实现（Markdown 文件）

基于 `aiofiles` 和 `pathlib`，提供安全、原子化的文件操作。

```python
# stores/impl/file.py
import aiofiles
import aiofiles.os
from pathlib import Path
import re

class AioFileStore:
    def __init__(self, root: Path):
        self.root = root

    async def read(self, path: str) -> str:
        full = self.root / path
        if not await aiofiles.os.path.exists(full):
            raise StoreError(StoreErrorCode.NOT_FOUND, f"file {path} not found")
        async with aiofiles.open(full, "r") as f:
            return await f.read()

    async def write(self, path: str, content: str) -> None:
        full = self.root / path
        await aiofiles.os.makedirs(full.parent, exist_ok=True)
        # 原子写入：先写临时文件再重命名
        tmp = full.with_suffix(full.suffix + ".tmp")
        async with aiofiles.open(tmp, "w") as f:
            await f.write(content)
        await aiofiles.os.replace(tmp, full)      # 原子操作

    async def delete(self, path: str) -> None:
        full = self.root / path
        try:
            await aiofiles.os.remove(full)
        except FileNotFoundError:
            raise StoreError(StoreErrorCode.NOT_FOUND, f"file {path} not found")

    async def list(self, directory: str, pattern: str = "*") -> list[str]:
        dir_path = self.root / directory
        files = []
        async for f in aiofiles.os.scandir(dir_path):
            if f.is_file() and Path(f.name).match(pattern):
                files.append(str(Path(directory) / f.name))
        return files
```

上层使用时可以自行决定文件内容格式（Markdown、JSON 等）。这个实现本身完全是通用文件存储。

---

## 4. PostgreSQL 实现（SQLAlchemy Core + asyncpg）

### 4.1 引擎与会话管理

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

engine = create_async_engine(
    "postgresql+asyncpg://user:pass@localhost/db",
    echo=False,
    pool_size=20,
    max_overflow=10,
)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)
```

### 4.2 通用表结构（示例）

```python
from sqlalchemy import Table, Column, String, DateTime, JSON, MetaData, func

metadata = MetaData()
kv_table = Table(
    "kv_store",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", JSON, nullable=False),
    Column("created_at", DateTime, nullable=False, server_default=func.now()),
    Column("updated_at", DateTime, nullable=False, server_default=func.now(), onupdate=func.now()),
)
```

### 4.3 基于该表的 `KeyValueStore` 实现

```python
from sqlalchemy import select, insert, update, delete
from datetime import datetime

class SqlKeyValueStore:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        self._sf = session_factory

    async def get(self, key: str) -> Record:
        async with self._sf() as session:
            row = (await session.execute(
                select(kv_table).where(kv_table.c.key == key)
            )).fetchone()
            if not row:
                raise StoreError(StoreErrorCode.NOT_FOUND, f"key {key} not found")
            return Record(
                key=row.key,
                value=row.value,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )

    async def set(self, key: str, value: Any) -> None:
        async with self._sf() as session:
            stmt = insert(kv_table).values(
                key=key, value=value,
                created_at=datetime.utcnow(), updated_at=datetime.utcnow()
            ).on_conflict_do_update(
                index_elements=["key"],
                set_={"value": value, "updated_at": datetime.utcnow()}
            )
            await session.execute(stmt)
            await session.commit()

    async def delete(self, key: str) -> None:
        async with self._sf() as session:
            await session.execute(delete(kv_table).where(kv_table.c.key == key))
            await session.commit()

    async def list(self, prefix: str | None = None) -> list[Record]:
        async with self._sf() as session:
            query = select(kv_table)
            if prefix:
                query = query.where(kv_table.c.key.startswith(prefix))
            rows = (await session.execute(query)).fetchall()
            return [
                Record(
                    key=row.key,
                    value=row.value,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]
```

### 4.4 使用 `async with` 管理事务边界

```python
# 调用方可传入外部 session，复用事务
async def get(self, key: str, session: AsyncSession | None = None) -> Record:
    if session:
        return await self._get(key, session)
    async with self._sf() as s:
        return await self._get(key, s)
```

### 4.5 Agent 记忆系统专用表结构（PostgreSQL 15+）

以下为记忆模块的**纯存储层**设计，作为单一可靠真相源，支撑多平台用户（QQ/手机/Web）统一身份、对话历史、记忆事件的持久化存储。

#### 4.5.1 设计原则

| 原则 | 做法 | 理由 |
|------|------|------|
| **全局唯一 ID** | UUID v7 (时间戳前缀 + 随机) | 跨平台、跨表唯一，免中心化生成，B-tree 索引友好 |
| **用户与平台身份解耦** | `users` 主表 + `user_identities` 关联表 | 同一用户可绑定 QQ 号、手机号、微信等多种平台，避免将业务标识符作为主键 |
| **敏感数据最小存储** | 验证码明文只存 Redis，数据库仅存 hash 用于审计 | 防止泄露，满足安全合规 |
| **时间序列有序** | 所有表包含 `created_at`、合理索引 | 支撑按时间窗口查询、消息回溯 |
| **软性状态** | 使用 `status` 字段而非物理删除 | 便于恢复与审计 |
| **扩展性预留** | JSONB 字段、可选的 pgvector 向量字段 | 允许动态属性、未来语义检索 |

#### 4.5.2 核心表 DDL

```sql
-- 启用扩展
CREATE EXTENSION IF NOT EXISTS "pgcrypto";    -- 用于 gen_random_uuid() 生成 v4
-- 若使用 UUID v7，请安装: CREATE EXTENSION "pg_uuidv7";
-- 若使用向量检索，请安装: CREATE EXTENSION "vector";

-- ===========================
-- 1. 用户主表
-- ===========================
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),  -- 建议替换为 uuid_generate_v7()
    nickname      VARCHAR(100),
    avatar_url    TEXT,
    timezone      VARCHAR(50) DEFAULT 'Asia/Shanghai',
    status        VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active','suspended','deleted')),
    last_active_at TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_users_status_active ON users(status, last_active_at DESC);

-- ===========================
-- 2. 用户多平台身份关联表
-- ===========================
CREATE TABLE user_identities (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform      VARCHAR(20) NOT NULL,         -- 'qq', 'phone', 'wechat', 'web' 等
    identifier    VARCHAR(255) NOT NULL,        -- QQ号、手机号、openid 等
    is_primary    BOOLEAN DEFAULT false,        -- 是否为主身份
    verified_at   TIMESTAMPTZ,                  -- 验证通过时间
    created_at    TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_platform_identifier UNIQUE (platform, identifier)
);
CREATE INDEX idx_identities_user    ON user_identities(user_id);
CREATE INDEX idx_identities_lookup  ON user_identities(platform, identifier);

-- ===========================
-- 3. 手机验证码审计表（明文仅存在于 Redis）
-- ===========================
CREATE TABLE phone_verifications (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    phone         VARCHAR(20) NOT NULL,
    code_hash     VARCHAR(128) NOT NULL,        -- 验证码 SHA-256 哈希值
    purpose       VARCHAR(50) DEFAULT 'login',
    attempts      INT DEFAULT 0,
    max_attempts  INT DEFAULT 5,
    verified      BOOLEAN DEFAULT false,
    expires_at    TIMESTAMPTZ NOT NULL,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_verif_phone_expires ON phone_verifications(phone, expires_at DESC);

-- ===========================
-- 4. 会话表
-- ===========================
CREATE TABLE sessions (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform      VARCHAR(20) NOT NULL,          -- 会话来源平台
    title         VARCHAR(500),                  -- 可由 LLM 生成会话标题
    status        VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active','completed','expired')),
    message_count INT DEFAULT 0,
    started_at    TIMESTAMPTZ DEFAULT now(),
    ended_at      TIMESTAMPTZ,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_sessions_user_time ON sessions(user_id, started_at DESC);
CREATE INDEX idx_sessions_active    ON sessions(status, user_id) WHERE status = 'active';

-- ===========================
-- 5. 消息流水表
-- ===========================
CREATE TABLE messages (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    role          VARCHAR(20) NOT NULL,            -- user, assistant, system, tool
    content       TEXT NOT NULL,
    metadata      JSONB DEFAULT '{}',              -- token数量、模型版本、工具调用详情
    token_count   INT,
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_messages_session_time ON messages(session_id, created_at);

-- ===========================
-- 6. 记忆事件表（结构化事实存储）
-- ===========================
CREATE TABLE memory_events (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    session_id    UUID REFERENCES sessions(id),
    event_type    VARCHAR(50) NOT NULL,            -- 'fact','preference','decision','task_result'
    subject       VARCHAR(500) NOT NULL,           -- 记忆主体
    predicate     VARCHAR(200),                    -- 关系
    object        JSONB NOT NULL,                  -- 记忆值（支持复杂结构）
    confidence    FLOAT DEFAULT 1.0,               -- 置信度 0-1
    source        VARCHAR(50) DEFAULT 'conversation',
    source_msg_id UUID REFERENCES messages(id),
    embedding     vector(1536),                   -- 可选，需 pgvector 扩展
    expires_at    TIMESTAMPTZ,                     -- NULL 表示永久
    created_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_memory_user_event  ON memory_events(user_id, event_type);
CREATE INDEX idx_memory_user_conf   ON memory_events(user_id, confidence DESC);
CREATE INDEX idx_memory_session     ON memory_events(session_id);
-- 向量索引（按需创建）
-- CREATE INDEX idx_memory_embedding ON memory_events USING hnsw (embedding vector_cosine_ops);

-- ===========================
-- 7. 用户画像表（聚合特征）
-- ===========================
CREATE TABLE user_profiles (
    user_id             UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    preferences         JSONB DEFAULT '{}',        -- 语言、回答风格等
    knowledge_tags      TEXT[] DEFAULT '{}',       -- 兴趣标签数组
    behavioral_summary  JSONB DEFAULT '{}',        -- 行为摘要（活跃时段、平均消息长度等）
    custom_context      JSONB DEFAULT '{}',        -- 炒股偏好、SQL方言偏好等
    updated_at          TIMESTAMPTZ DEFAULT now()
);

-- ===========================
-- 8. 知识文件索引表（关联 MD 文件系统）
-- ===========================
CREATE TABLE knowledge_files (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    file_path     VARCHAR(500) NOT NULL UNIQUE,    -- 文件系统内相对路径
    file_type     VARCHAR(50) DEFAULT 'md',
    category      VARCHAR(100),                    -- 'rule', 'knowledge', 'user_specific', 'task_template'
    owner_user_id UUID REFERENCES users(id),      -- NULL 表示全局共享
    tags          TEXT[] DEFAULT '{}',
    last_loaded_at TIMESTAMPTZ,
    checksum      VARCHAR(64),                    -- 内容哈希，用于增量更新
    created_at    TIMESTAMPTZ DEFAULT now(),
    updated_at    TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_kf_category ON knowledge_files(category);
CREATE INDEX idx_kf_owner    ON knowledge_files(owner_user_id);
```

#### 4.5.3 表关系与整体视图

```
users (1) ---< user_identities (N)    一个用户绑定多个平台账号
users (1) ---< sessions (N)           一个用户拥有多个会话
sessions (1) ---< messages (N)        一个会话包含多条消息
users (1) ---< memory_events (N)      一个用户关联多条记忆事件
messages (1) ---< memory_events (N, nullable) 记忆可溯源到某条消息
users (1) ---  user_profiles (1)      用户画像一对一扩展
users (0,1) -< knowledge_files (N)   文件可指定所有者（NULL=全局）
```

#### 4.5.4 关键场景下的数据流转（仅存储视角）

1. **QQ 用户首次对话，且需绑定手机号**  
   - 应用层根据 QQ 号在 `user_identities` 中查找 `user_id`，无则创建 `users` → 插入 `user_identities` (platform='qq')。  
   - 发送验证码：Redis 存明文 (key=`verify_code:手机号`)，`phone_verifications` 存 code_hash 及过期时间。  
   - 验证通过后，将 `platform='phone'` 的身份插入 `user_identities`，完成绑定。

2. **对话消息落盘**  
   - `sessions` 记录会话元信息；`messages` 逐条写入，`message_count` 递增。  
   - 任务执行结果作为 `memory_events` 写入（event_type='task_result'），关联对应的 `session_id` 和 `source_msg_id`。

3. **跨会话用户画像更新**  
   - 异步任务从 `messages` 和 `memory_events` 中提炼事实，合并入 `user_profiles` 的 JSONB 字段（如 `custom_context` 中更新“关注白酒板块”）。

#### 4.5.5 扩展性与性能说明

- **UUID 索引优化**：建议使用 UUID v7（时间有序），减少索引碎片；应用层批量写入时可指定 `uuid_generate_v7()`。
- **JSONB 灵活属性**：`user_profiles`、`memory_events.object` 等字段允许“记忆”内容动态变化，无需频繁 DDL。
- **pgvector 按需开启**：初期 `embedding` 字段保持 NULL，当需要语义记忆搜索时再安装 `pgvector` 并建立向量索引，对现有表零影响。
- **分区策略**：若 `messages` 或 `memory_events` 单表过大，可按 `created_at` 范围分区或按 `user_id` 做哈希分区，利用原生 PostgreSQL 表继承实现。

---

## 5. Redis 集成（缓存与 Pub/Sub）

### 5.1 缓存（通用 Key/Value 带 TTL）

```python
import redis.asyncio as aioredis
from datetime import timedelta

class RedisCache:
    def __init__(self, redis: aioredis.Redis, default_ttl: int = 300):
        self.redis = redis
        self.ttl = default_ttl

    async def get(self, key: str) -> bytes | None:
        return await self.redis.get(key)

    async def set(self, key: str, value: bytes, ttl: int | None = None) -> None:
        await self.redis.set(key, value, ex=ttl or self.ttl)

    async def delete(self, key: str) -> None:
        await self.redis.delete(key)
```

### 5.2 发布/订阅实现

```python
import asyncio

class RedisPubSub:
    def __init__(self, redis: aioredis.Redis):
        self.redis = redis
        self._handlers: dict[str, set[Handler]] = {}

    async def publish(self, topic: str, payload: bytes) -> None:
        await self.redis.publish(f"app:{topic}", payload)

    async def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers.setdefault(topic, set()).add(handler)
        # 启动监听任务（每主题一个）
        if len(self._handlers[topic]) == 1:
            asyncio.create_task(self._listen(topic))

    async def unsubscribe(self, topic: str, handler: Handler) -> None:
        if topic in self._handlers:
            self._handlers[topic].discard(handler)
            if not self._handlers[topic]:
                del self._handlers[topic]

    async def _listen(self, topic: str) -> None:
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(f"app:{topic}")
        try:
            async for msg in pubsub.listen():
                if msg["type"] == "message":
                    for h in self._handlers.get(topic, set()):
                        await h(msg["data"])
        finally:
            await pubsub.unsubscribe(f"app:{topic}")
```

---

## 6. 错误处理标准化

所有实现都必须将底层异常转换为统一的 `StoreError`：

```python
from enum import StrEnum

class StoreErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"
    DUPLICATE = "DUPLICATE"
    CONNECTION_FAILED = "CONNECTION_FAILED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_DATA = "INVALID_DATA"

class StoreError(Exception):
    def __init__(self, code: StoreErrorCode, message: str, original: Exception | None = None):
        self.code = code
        self.message = message
        self.original = original
        super().__init__(f"[{code}] {message}")

def normalize_db_error(err: Exception, entity: str, operation: str) -> StoreError:
    import asyncpg.exceptions
    
    if isinstance(err, asyncpg.exceptions.UniqueViolationError):
        return StoreError(StoreErrorCode.DUPLICATE, f"{entity} duplicate on {operation}", err)
    elif isinstance(err, asyncpg.exceptions.ForeignKeyViolationError):
        return StoreError(StoreErrorCode.INVALID_DATA, f"{entity} references non-existent record on {operation}", err)
    elif isinstance(err, asyncpg.exceptions.NotNullViolationError):
        return StoreError(StoreErrorCode.INVALID_DATA, f"{entity} missing required field on {operation}", err)
    elif isinstance(err, (asyncpg.exceptions.ConnectionDoesNotExistError, 
                         asyncpg.exceptions.CannotConnectNowError)):
        return StoreError(StoreErrorCode.CONNECTION_FAILED, f"{entity} {operation} failed: database connection error", err)
    
    return StoreError(StoreErrorCode.CONNECTION_FAILED, f"{entity} {operation} failed: {str(err)}", err)
```

上层调用只检查 `StoreError` 和 `StoreErrorCode`，与后端彻底解耦。

---

## 7. 依赖注入与组装

使用手动构造函数注入，将具体实现绑定到协议，并在启动时完成组装：

```python
from dataclasses import dataclass
from pathlib import Path

@dataclass
class AppConfig:
    memory_dir: Path
    database_url: str
    redis_url: str

@dataclass
class InfraContainer:
    file_store: FileStore
    kv_store: KeyValueStore
    pubsub: PubSub
    redis_cache: RedisCache
    config: AppConfig

    @classmethod
    async def build(cls, config: AppConfig) -> "InfraContainer":
        # 文件存储
        file_store = AioFileStore(root=config.memory_dir)
        
        # Postgres
        engine = create_async_engine(config.database_url, echo=False, pool_size=20, max_overflow=10)
        sf = async_sessionmaker(engine, expire_on_commit=False)
        kv_store = SqlKeyValueStore(sf)
        
        # Redis
        redis = aioredis.from_url(config.redis_url, decode_responses=False)
        pubsub = RedisPubSub(redis)
        redis_cache = RedisCache(redis)

        return cls(
            file_store=file_store,
            kv_store=kv_store,
            pubsub=pubsub,
            redis_cache=redis_cache,
            config=config,
        )
```

至此，您拥有了一个包含**通用存储基础设施**和**Agent记忆系统专用数据库实现**的完整存储层。记忆层（例如“何时保存”、“如何检索”、“记忆类型定义”）可以完全由您在上层自主设计，并通过这些协议调用。
