"""
PostgreSQL 存储 —— SQLAlchemy Core + asyncpg 驱动。

提供两个层次的存储能力：
  A. 通用键值存储 —— SqlKeyValueStore 实现 KeyValueStore 协议。
     基于 kv_store 表，提供基础的 get/set/delete/list 操作。
  B. Agent 记忆专用表 —— 8 张核心表 + 12 个索引，支撑多平台用户、
     会话历史、记忆事件、用户画像的完整持久化。

依赖：pip install sqlalchemy asyncpg

当 database_url 为空时，container.py 自动回退到 InMemoryKVStore，
因此本模块的导入是惰性的 —— 仅在 database_url 非空时才加载。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,         # PostgreSQL JSONB 列类型，支持 JSON 查询和索引
    Boolean,
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .protocols import KeyValueStore, Record, StoreError, StoreErrorCode, normalize_db_error

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# 共享元数据 —— 所有表注册在同一 MetaData 实例，便于 create_all 一次性建表
# ═══════════════════════════════════════════════════════════════════════════════

metadata = MetaData()

# ═══════════════════════════════════════════════════════════════════════════════
# A. 通用键值表 —— 实现 KeyValueStore 协议所需的最小表
# ═══════════════════════════════════════════════════════════════════════════════

kv_table = Table(
    "kv_store",
    metadata,
    Column("key", String, primary_key=True),                                  # 唯一键，由上层定义命名规范
    Column("value", JSON, nullable=False),                                    # 任意 JSON 可序列化的值
    Column("created_at", DateTime, nullable=False, server_default=func.now()), # 创建时间，数据库侧默认值
    Column("updated_at", DateTime, nullable=False, server_default=func.now()), # 更新时间，每次 upsert 需手动更新
)

# ═══════════════════════════════════════════════════════════════════════════════
# B. Agent 记忆系统专用表 —— 对应 docs/version/v5/durable.md §4.5.2
#
# 设计原则：
#   - 全局唯一 UUID 主键（建议 v7 时间有序），由应用层生成
#   - users 与 user_identities 解耦 —— 同一用户可绑定 QQ/手机/Web 多平台
#   - 敏感数据最小存储 —— 验证码明文仅存 Redis，数据库仅存 SHA-256 哈希
#   - JSONB 灵活字段 —— 画像、记忆内容可动态扩展，无需 DDL 变更
#   - 软删除 —— 使用 status 字段替代物理删除，便于恢复和审计
#   - 扩展预留 —— memory_events.embedding 可选向量字段（需 pgvector 扩展）
# ═══════════════════════════════════════════════════════════════════════════════

# ── 用户主表 ───────────────────────────────────────────────────────────────
# 存储用户核心信息，与具体的平台账号（QQ/手机/微信）解耦
users_table = Table(
    "users",
    metadata,
    Column("id", String, primary_key=True),              # UUID，应用层生成（建议 uuid v7）
    Column("nickname", String(100)),                     # 用户昵称
    Column("avatar_url", Text),                          # 头像 URL
    Column("timezone", String(50), default="Asia/Shanghai"),  # 时区，默认东八区
    Column("status", String(20), default="active"),      # 账号状态：active | suspended | deleted
    Column("last_active_at", DateTime(timezone=True)),   # 最后活跃时间，用于排序活跃用户
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 用户多平台身份关联表 ──────────────────────────────────────────────────
# 同一 user_id 可关联多条记录：QQ号、手机号、微信 openid 等
# (platform, identifier) 为唯一约束，防止同一身份重复绑定
user_identities_table = Table(
    "user_identities",
    metadata,
    Column("id", String, primary_key=True),              # UUID
    Column("user_id", String, nullable=False),            # 关联 users.id
    Column("platform", String(20), nullable=False),       # 平台类型：qq | phone | wechat | web
    Column("identifier", String(255), nullable=False),    # 平台侧用户标识：QQ号、手机号、openid
    Column("is_primary", Boolean, default=False),         # 是否为主身份（用于多端优先级）
    Column("verified_at", DateTime(timezone=True)),       # 身份验证通过时间（手机验证码验证后）
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 手机验证码审计表 ──────────────────────────────────────────────────────
# 仅存储 SHA-256 哈希用于审计，验证码明文通过 Redis 缓存（有过期时间）
phone_verifications_table = Table(
    "phone_verifications",
    metadata,
    Column("id", String, primary_key=True),              # UUID
    Column("phone", String(20), nullable=False),          # 手机号
    Column("code_hash", String(128), nullable=False),     # 验证码 SHA-256 哈希值（不可逆）
    Column("purpose", String(50), default="login"),       # 用途：login | bind | reset_password
    Column("attempts", Integer, default=0),               # 当前已尝试次数
    Column("max_attempts", Integer, default=5),           # 最大尝试次数，超出后验证码失效
    Column("verified", Boolean, default=False),           # 是否已验证通过
    Column("expires_at", DateTime(timezone=True), nullable=False),  # 过期时间（必填）
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 会话表 ─────────────────────────────────────────────────────────────────
# 记录每次对话会话的元信息。title 可由 LLM 自动生成。
sessions_table = Table(
    "sessions",
    metadata,
    Column("id", String, primary_key=True),               # UUID
    Column("user_id", String, nullable=False),             # 关联 users.id
    Column("platform", String(20), nullable=False),        # 会话来源平台：napcat | web | console
    Column("title", String(500)),                          # 会话标题，可由 LLM 根据首条消息生成
    Column("status", String(20), default="active"),        # 状态：active | completed | expired
    Column("message_count", Integer, default=0),           # 消息计数，append 时递增
    Column("started_at", DateTime(timezone=True), server_default=func.now()),
    Column("ended_at", DateTime(timezone=True)),           # 会话结束时间
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 消息流水表 ─────────────────────────────────────────────────────────────
# 每条消息一行，按 session_id + created_at 排序即得完整对话历史
messages_table = Table(
    "messages",
    metadata,
    Column("id", String, primary_key=True),               # UUID
    Column("session_id", String, nullable=False),          # 关联 sessions.id
    Column("role", String(20), nullable=False),            # 角色：user | assistant | system | tool
    Column("content", Text, nullable=False),               # 消息内容（文本，不做长度限制）
    Column("metadata", JSON),                              # JSONB 扩展：token数量、模型版本、工具调用详情
    Column("token_count", Integer),                        # 该消息消耗的 token 数（用于成本统计）
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 记忆事件表 ─────────────────────────────────────────────────────────────
# 结构化事实存储，是记忆系统的核心表。
# 使用 SPO（主-谓-宾）模型：subject + predicate + object。
# 例如：用户(nono) — 偏好(语言) → 中文
memory_events_table = Table(
    "memory_events",
    metadata,
    Column("id", String, primary_key=True),               # UUID
    Column("user_id", String, nullable=False),             # 关联 users.id —— 记忆属于谁
    Column("session_id", String),                          # 关联 sessions.id —— 记忆产生于哪次对话
    Column("event_type", String(50), nullable=False),      # 事件类型：fact | preference | decision | task_result
    Column("subject", String(500), nullable=False),         # 记忆主体（如 "用户"、"任务#123"）
    Column("predicate", String(200)),                      # 关系/谓词（如 "偏好语言"、"执行结果"）
    Column("object", JSON, nullable=False),                # 记忆值，支持复杂结构（如 {"language": "zh", "style": "concise"}）
    Column("confidence", Float, default=1.0),              # 置信度 0-1，越接近 1 越确定
    Column("source", String(50), default="conversation"),  # 来源：conversation | inference | explicit
    Column("source_msg_id", String),                       # 可溯源到哪条消息
    # Column("embedding", ...),                            # 可选：pgvector 向量字段，按需启用
    Column("expires_at", DateTime(timezone=True)),         # 过期时间，NULL 表示永久记忆
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 用户画像表 ─────────────────────────────────────────────────────────────
# 聚合特征存储，与 users 一对一。
# 由异步任务从 messages 和 memory_events 中提炼并更新。
user_profiles_table = Table(
    "user_profiles",
    metadata,
    Column("user_id", String, primary_key=True),           # 关联 users.id（一对一）
    Column("preferences", JSON),                            # 偏好：{language, reply_style, verbosity}
    Column("knowledge_tags", JSON),                         # 兴趣标签数组：["编程", "股票", "游戏"]
    Column("behavioral_summary", JSON),                     # 行为摘要：{active_hours, avg_msg_length, ...}
    Column("custom_context", JSON),                         # 自定义上下文：{sql_dialect, stock_watchlist, ...}
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# ── 知识文件索引表 ─────────────────────────────────────────────────────────
# 关联文件系统中的 MD 知识文件，支持按分类、所有者、标签检索。
knowledge_files_table = Table(
    "knowledge_files",
    metadata,
    Column("id", String, primary_key=True),               # UUID
    Column("file_path", String(500), nullable=False, unique=True),  # 文件系统内相对路径（唯一）
    Column("file_type", String(50), default="md"),         # 文件类型：md | yaml | json | txt
    Column("category", String(100)),                       # 分类：rule | knowledge | user_specific | task_template
    Column("owner_user_id", String),                       # NULL 表示全局共享，非 NULL 表示用户专属
    Column("tags", JSON),                                  # 标签数组：["python", "sql"]
    Column("last_loaded_at", DateTime(timezone=True)),     # 最后加载到内存的时间
    Column("checksum", String(64)),                        # 内容哈希（SHA-256），用于增量更新检测
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), server_default=func.now()),
)

# ═══════════════════════════════════════════════════════════════════════════════
# 索引定义 —— 随表一起在 ensure_schema() 中创建
#
# 注：SQLAlchemy Table 对象不支持部分索引（WHERE 条件）和 UNIQUE IF NOT EXISTS，
#     因此这些索引用原生 DDL 字符串定义，在 ensure_schema() 中逐一执行。
# ═══════════════════════════════════════════════════════════════════════════════

_INDEX_DDL: list[str] = [
    # users: 活跃用户按最后活跃时间倒排
    "CREATE INDEX IF NOT EXISTS idx_users_status_active ON users(status, last_active_at DESC)",
    # user_identities: 按 user_id 查所有绑定；按 platform+identifier 精确查找
    "CREATE INDEX IF NOT EXISTS idx_identities_user ON user_identities(user_id)",
    "CREATE INDEX IF NOT EXISTS idx_identities_lookup ON user_identities(platform, identifier)",
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_platform_identifier ON user_identities(platform, identifier)",
    # phone_verifications: 按手机号查最近验证记录
    "CREATE INDEX IF NOT EXISTS idx_verif_phone_expires ON phone_verifications(phone, expires_at DESC)",
    # sessions: 按用户时间轴；按活跃状态筛选
    "CREATE INDEX IF NOT EXISTS idx_sessions_user_time ON sessions(user_id, started_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_active ON sessions(status, user_id) WHERE status = 'active'",
    # messages: 按会话时间轴排序（对话历史回溯的核心查询）
    "CREATE INDEX IF NOT EXISTS idx_messages_session_time ON messages(session_id, created_at)",
    # memory_events: 按用户+事件类型；按置信度倒排；按会话追溯
    "CREATE INDEX IF NOT EXISTS idx_memory_user_event ON memory_events(user_id, event_type)",
    "CREATE INDEX IF NOT EXISTS idx_memory_user_conf ON memory_events(user_id, confidence DESC)",
    "CREATE INDEX IF NOT EXISTS idx_memory_session ON memory_events(session_id)",
    # knowledge_files: 按分类；按所有者
    "CREATE INDEX IF NOT EXISTS idx_kf_category ON knowledge_files(category)",
    "CREATE INDEX IF NOT EXISTS idx_kf_owner ON knowledge_files(owner_user_id)",
]


# ═══════════════════════════════════════════════════════════════════════════════
# 引擎与会话工厂 —— 启动时由 container.py 调用
# ═══════════════════════════════════════════════════════════════════════════════

def create_engine(database_url: str, **kwargs: Any):
    """创建异步 SQLAlchemy 引擎。

    默认配置适用于中等负载：
      - pool_size=20：最多 20 个持久连接
      - max_overflow=10：峰值时可额外创建 10 个连接（共 30）
      - echo=False：不打印 SQL 日志（生产环境）

    Args:
        database_url: PostgreSQL 连接串，
            格式: "postgresql+asyncpg://user:pass@host:port/dbname"
        **kwargs: 覆盖默认的 pool_size / max_overflow / echo 等。

    Returns:
        sqlalchemy.ext.asyncio.AsyncEngine 实例。
    """
    return create_async_engine(
        database_url,
        echo=kwargs.pop("echo", False),
        pool_size=kwargs.pop("pool_size", 20),
        max_overflow=kwargs.pop("max_overflow", 10),
        **kwargs,
    )


def create_session_factory(engine):
    """基于引擎创建异步会话工厂。

    expire_on_commit=False：提交后不使对象过期，
    允许在事务外继续访问已加载的属性。
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def ensure_schema(engine) -> None:
    """确保所有表和索引存在（幂等操作）。

    在应用启动时调用一次即可。内部流程：
      1. metadata.create_all() → 创建所有 Table 对象定义的表
      2. 逐个执行 _INDEX_DDL → 创建 SQLAlchemy 不支持的索引（部分索引、UNIQUE IF NOT EXISTS）
    """
    async with engine.begin() as conn:
        # 创建所有表（IF NOT EXISTS 语义，幂等）
        await conn.run_sync(metadata.create_all)
        # 创建索引（IF NOT EXISTS 语义，幂等）
        for idx_ddl in _INDEX_DDL:
            await conn.execute(await _raw_ddl(idx_ddl))


async def _raw_ddl(sql: str):
    """将原生 SQL 字符串包装为 SQLAlchemy text 子句。

    用于执行 metadata 不直接支持的 DDL（如部分索引 WHERE 条件）。
    """
    from sqlalchemy import text
    return text(sql)


# ═══════════════════════════════════════════════════════════════════════════════
# KeyValueStore 的 PostgreSQL 实现
# ═══════════════════════════════════════════════════════════════════════════════

class SqlKeyValueStore:
    """基于 PostgreSQL 的键值存储，实现 KeyValueStore 协议。

    使用 SQLAlchemy Core（非 ORM），所有操作通过 async session 执行。
    线程安全：每次操作创建独立的 session 上下文，不存在共享状态。

    Upsert 语义：set() 使用 INSERT ... ON CONFLICT DO UPDATE，
    对同一 key 的多次 set 不会产生重复行。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """
        Args:
            session_factory: async_sessionmaker 实例，每次操作通过它获取新 session。
        """
        self._sf = session_factory

    async def get(self, key: str) -> Record:
        """按 key 查询单条记录。

        流程：SELECT * FROM kv_store WHERE key = $1 → 取一行 → 映射为 Record。
        找不到时抛出 StoreError(NOT_FOUND)，不返回 None。
        """
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
        """写入或更新记录（upsert）。

        INSERT ... ON CONFLICT (key) DO UPDATE：
          - key 不存在 → 新插入一行，创建 created_at 和 updated_at
          - key 已存在 → 更新 value 和 updated_at，保留原 created_at
        """
        now = datetime.now(timezone.utc)
        async with self._sf() as session:
            stmt = (
                insert(kv_table)
                .values(key=key, value=value, created_at=now, updated_at=now)
                .on_conflict_do_update(
                    index_elements=["key"],             # 冲突检测：以 key 主键为准
                    set_={"value": value, "updated_at": now},  # 冲突时更新这两列
                )
            )
            await session.execute(stmt)
            await session.commit()

    async def delete(self, key: str) -> None:
        """删除记录。key 不存在时静默成功（幂等 —— 不报错）。"""
        async with self._sf() as session:
            await session.execute(delete(kv_table).where(kv_table.c.key == key))
            await session.commit()

    async def list(self, prefix: str | None = None) -> list[Record]:
        """列出记录，可选按前缀过滤。

        当 prefix 非空时，使用 WHERE key LIKE 'prefix%' 进行前缀匹配。
        结果按 key 自然排序。
        """
        async with self._sf() as session:
            query = select(kv_table)
            if prefix:
                # LIKE 'prefix%' 前缀匹配，利用 B-tree 索引前缀扫描
                query = query.where(kv_table.c.key.startswith(prefix))
            rows = (await session.execute(query)).fetchall()
            return [
                Record(
                    key=r.key,
                    value=r.value,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
                for r in rows
            ]
