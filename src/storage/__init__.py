"""
存储基础设施层 —— Redis 缓存 + 本地文件存储。

所有持久化操作通过 protocols.py 定义的接口进行。
当前实现: RedisCache (KeyValueStore), LocalFileStore (FileStore)。
"""
from .protocols import Handler
from .redis import RedisCache
from .file_store import LocalFileStore

__all__ = [
    "Handler",
    "RedisCache",
    "LocalFileStore",
]
