"""
存储层协议定义 —— 所有后端的统一抽象。

上层代码仅通过 typing.Protocol 接口操作存储，绝不直接依赖具体实现。
这使得切换后端（文件 / PostgreSQL / Redis）时无需修改任何业务代码。

设计原则：
  1. 所有方法均为 async —— 适应 IO 密集型场景（网络请求、磁盘读写）。
  2. 错误统一使用 StoreError —— 绝不返回 None 表示"没找到"或泄漏驱动异常。
  3. 协议方法只声明签名，不提供默认实现 —— 每个后端自行实现。
  4. 文件存储使用 POSIX 风格路径，不假定任何文件格式（MD/JSON/YAML 均可）。

依赖链：本模块零外部依赖，只使用标准库 typing / dataclasses / enum。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


# ═══════════════════════════════════════════════════════════════════════════════
# 错误体系 —— 所有后端必须将底层异常转换为 StoreError
# ═══════════════════════════════════════════════════════════════════════════════

class StoreErrorCode(StrEnum):
    """存储错误码枚举，每个错误码对应一种可恢复/不可恢复的场景。

    上层只检查 code 字段，不依赖异常类型或消息内容。
    """
    NOT_FOUND = "NOT_FOUND"                # 键/文件不存在（可恢复 —— 调用方可返回默认值）
    DUPLICATE = "DUPLICATE"                # 唯一约束冲突（如重复插入同一主键）
    CONNECTION_FAILED = "CONNECTION_FAILED"  # 数据库/Redis 连接失败（需重试或告警）
    PERMISSION_DENIED = "PERMISSION_DENIED"  # 权限不足（如文件路径逃逸沙箱根目录）
    INVALID_DATA = "INVALID_DATA"          # 数据约束违反（外键不存在、非空字段为空）


class StoreError(Exception):
    """统一的存储异常，所有后端必须抛出此类型（或其子类）。

    区别于底层驱动的原生异常（如 asyncpg.UniqueViolationError），
    StoreError 屏蔽了具体后端的异常类型，上层只需捕获这一个异常即可。

    Attributes:
        code: 错误码枚举，用于分类处理（重试 / 返回404 / 500）。
        message: 人类可读的错误描述，可写入日志或返回给 API 消费者。
        original: 保留原始驱动异常，用于调试和日志记录；生产环境不应暴露给外部。
    """

    def __init__(
        self,
        code: StoreErrorCode,
        message: str,
        original: Exception | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.original = original
        # 父类初始化：格式化输出 "[错误码] 错误描述"，如 "[NOT_FOUND] key xxx not found"
        super().__init__(f"[{code.value}] {message}")


# ═══════════════════════════════════════════════════════════════════════════════
# 数据类型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Record:
    """通用键值记录 —— KeyValueStore 协议的唯一数据结构。

    不假定任何业务实体（不是 User、不是 Session），
    上层自行将 JSON 可序列化的 value 反序列化为具体领域对象。

    Attributes:
        key: 唯一键，由上层定义命名规范（如 "session:abc123"、"config:theme"）。
        value: 任意 JSON 可序列化的值（dict / list / str / int / float / bool / None）。
        created_at: 记录首次创建时间（UTC）。
        updated_at: 记录最后更新时间（UTC）。
    """
    key: str
    value: Any          # JSON-serialisable
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════════
# 存储协议（typing.Protocol） —— 每个协议定义一组"鸭子类型"方法签名
# ═══════════════════════════════════════════════════════════════════════════════

# 发布/订阅的消息处理器类型别名：接收 bytes 载荷，异步无返回
Handler = Callable[[bytes], Awaitable[None]]


@runtime_checkable
class KeyValueStore(Protocol):
    """键值存储协议 —— 最通用的存储抽象。

    适用场景：配置缓存、会话元数据、简单索引、分布式锁元信息。
    不适用：复杂查询、全文搜索、向量检索（需直接使用 PostgreSQL 原生功能）。
    """

    async def get(self, key: str) -> Record:
        """按 key 获取单条记录。

        Raises:
            StoreError(NOT_FOUND): 键不存在时抛出，绝不返回 None。
        """
        ...

    async def set(self, key: str, value: Any) -> None:
        """写入或更新一条记录（upsert 语义）。

        如果 key 已存在则覆盖更新 updated_at；
        如果 key 不存在则新建并设置 created_at。
        """
        ...

    async def delete(self, key: str) -> None:
        """删除一条记录。如果 key 不存在则静默成功（幂等）。"""
        ...

    async def list(self, prefix: str | None = None) -> list[Record]:
        """列出记录。

        Args:
            prefix: 可选前缀过滤。如 prefix="session:" 只返回以 "session:" 开头的键。
                    传 None 则返回全部记录（注意性能——生产环境应始终传 prefix）。

        Returns:
            按 key 排序的 Record 列表。
        """
        ...


@runtime_checkable
class FileStore(Protocol):
    """文件存储协议 —— 面向通用文件的读写抽象。

    不假定文件格式（可以是 MD、JSON、YAML、纯文本）。
    上层自行决定文件的序列化格式和目录组织方式。

    路径约定：
      - 所有路径为 POSIX 风格相对路径（如 "sessions/abc.json"）。
      - 根目录由实现方在构造函数中指定，协议不关心根目录位置。
      - 路径遍历漏洞由具体实现防御。
    """

    async def read(self, path: str) -> str:
        """读取文件全部文本内容。

        Raises:
            StoreError(NOT_FOUND): 文件不存在。
        """
        ...

    async def write(self, path: str, content: str) -> None:
        """写入文件。父目录不存在时自动创建。

        实现应保证原子性：先写临时文件再 rename，避免读到半截内容。
        """
        ...

    async def delete(self, path: str) -> None:
        """删除文件。

        Raises:
            StoreError(NOT_FOUND): 文件不存在。
        """
        ...

    async def list(self, directory: str, pattern: str = "*") -> list[str]:
        """列出目录下的文件。

        Args:
            directory: 相对目录路径。
            pattern: glob 风格过滤（如 "*.json"、"sess_*.md"），默认 "*" 匹配所有。

        Returns:
            排序后的相对文件路径列表。目录不存在时返回空列表而非报错。
        """
        ...


@runtime_checkable
class PubSub(Protocol):
    """发布/订阅协议 —— 用于跨进程/跨服务的事件通知。

    适用场景：
      - Workflow 状态变更通知（session 完成 → 通知 WebSocket 推送）。
      - 缓存失效广播（用户画像更新 → 所有副本清除缓存）。
      - 实时消息路由（跨渠道消息桥接）。

    不适用场景：持久化消息队列（应使用 Temporal Signal 或专门的消息队列）。
    """

    async def publish(self, topic: str, payload: bytes) -> None:
        """向指定 topic 发布一条消息。

        Args:
            topic: 主题名，建议命名规范 "domain:entity:event" 如 "session:abc123:completed"。
            payload: 二进制载荷，由上层自行序列化（JSON / Protobuf / msgpack）。
        """
        ...

    async def subscribe(self, topic: str, handler: Handler) -> None:
        """订阅一个 topic，当有消息发布时回调 handler。

        允许同一 topic 注册多个 handler（广播语义）。
        首次订阅时启动后台监听任务。

        Args:
            topic: 订阅的主题名。
            handler: 异步回调函数，接收 bytes 载荷。
        """
        ...

    async def unsubscribe(self, topic: str, handler: Handler) -> None:
        """取消订阅。当 topic 下无剩余 handler 时停止后台监听任务。"""
        ...
