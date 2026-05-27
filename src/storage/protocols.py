"""
存储层协议定义 —— 所有后端的统一抽象。

上层代码仅通过 typing.Protocol 接口操作存储，绝不直接依赖具体实现。
这使得切换后端（文件 / PostgreSQL / Redis）时无需修改任何业务代码。

依赖链：本模块零外部依赖，只使用标准库 typing / dataclasses / enum。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable


# ═══════════════════════════════════════════════════════════════════════════════
# 错误体系
# ═══════════════════════════════════════════════════════════════════════════════

class StoreErrorCode(StrEnum):
    NOT_FOUND = "NOT_FOUND"  # 键/文件不存在


class StoreError(Exception):
    """统一的存储异常，屏蔽底层驱动的原生异常类型。"""

    def __init__(
        self,
        code: StoreErrorCode,
        message: str,
        original: Exception | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.original = original
        super().__init__(f"[{code.value}] {message}")


# ═══════════════════════════════════════════════════════════════════════════════
# 数据类型
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Record:
    """通用键值记录 —— KeyValueStore 协议的唯一数据结构。"""
    key: str
    value: Any          # JSON-serialisable
    created_at: datetime
    updated_at: datetime


# ═══════════════════════════════════════════════════════════════════════════════
# 存储协议
# ═══════════════════════════════════════════════════════════════════════════════

Handler = Callable[[bytes], Awaitable[None]]


@runtime_checkable
class KeyValueStore(Protocol):
    """键值存储协议。"""

    async def get(self, key: str) -> Record:
        """按 key 获取单条记录。key 不存在时抛出 StoreError(NOT_FOUND)。"""
        ...

    async def set(self, key: str, value: Any) -> None:
        """写入或更新一条记录（upsert 语义）。"""
        ...

    async def delete(self, key: str) -> None:
        """删除一条记录。key 不存在则静默成功（幂等）。"""
        ...

    async def list(self, prefix: str | None = None) -> list[Record]:
        """列出记录，可按前缀过滤。"""
        ...


@runtime_checkable
class FileStore(Protocol):
    """文件存储协议 —— 面向通用文件的读写抽象。

    路径约定：
      - 所有路径为 POSIX 风格相对路径（如 "sessions/abc.json"）。
      - 根目录由实现方在构造函数中指定。
      - 路径遍历漏洞由具体实现防御。
    """

    async def read(self, path: str) -> str:
        """读取文件全部文本内容。文件不存在时抛出 StoreError(NOT_FOUND)。"""
        ...

    async def write(self, path: str, content: str) -> None:
        """写入文件。父目录不存在时自动创建。"""
        ...

    async def delete(self, path: str) -> None:
        """删除文件。文件不存在时抛出 StoreError(NOT_FOUND)。"""
        ...

    async def list(self, directory: str, pattern: str = "*") -> list[str]:
        """列出目录下的文件。目录不存在时返回空列表。"""
        ...
