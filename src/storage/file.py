"""
文件系统存储 —— 基于 aiofiles 的异步文件读写。

核心特性：
  1. 原子写入：先写临时文件（.tmp 后缀），成功后再 os.replace 重命名。
     这保证读操作不会看到写了一半的脏数据（POSIX 原子重命名）。
  2. 路径沙箱：_resolve() 强制限制所有路径在 root 目录以内，
     拒绝 "../" 等目录遍历攻击。
  3. 延迟导入：aiofiles 仅在首次实例化或方法调用时导入，
     使得 protocols.py 和 container.py 可独立加载。

依赖：pip install aiofiles
"""
from __future__ import annotations

import logging
from pathlib import Path

from .protocols import StoreError, StoreErrorCode

logger = logging.getLogger(__name__)


class AioFileStore:
    """通用文件存储，实现 FileStore 协议。

    使用 POSIX 风格相对路径，root 目录在构造时固定。
    所有路径操作均解析到 root 之下，防止目录遍历漏洞。

    用法示例::

        store = AioFileStore(root=Path("data/memory"))
        await store.write("sessions/abc123.json", json_content)
        content = await store.read("sessions/abc123.json")
        files = await store.list("sessions", pattern="*.json")
    """

    def __init__(self, root: Path) -> None:
        """
        Args:
            root: 文件存储的根目录绝对路径。所有相对路径操作均限制在此目录内。
        """
        # 延迟导入：协议的 __init__.py 加载时不需要 aiofiles
        import aiofiles  # noqa: F401
        self.root = root

    # ══════════════════════════════════════════════════════════════════════
    # FileStore 协议实现
    # ══════════════════════════════════════════════════════════════════════

    async def read(self, path: str) -> str:
        """读取文件全部文本内容（UTF-8）。

        Args:
            path: root 下的相对文件路径，如 "sessions/abc.json"。

        Returns:
            文件的文本内容。

        Raises:
            StoreError(NOT_FOUND): 文件不存在。
        """
        import aiofiles
        import aiofiles.os

        full = self._resolve(path)
        if not await aiofiles.os.path.exists(full):
            raise StoreError(StoreErrorCode.NOT_FOUND, f"file {path} not found")
        async with aiofiles.open(full, "r") as f:
            return await f.read()

    async def write(self, path: str, content: str) -> None:
        """原子写入文件内容。

        写入流程：
          1. 确保父目录存在（递归创建）。
          2. 先写内容到一个 .tmp 临时文件。
          3. os.replace(tmp, full) 原子替换 —— 在 POSIX 系统上是原子操作。
          4. 如果写入失败，尽力清理临时文件。

        Args:
            path: root 下的相对文件路径。
            content: 要写入的文本内容（UTF-8）。
        """
        import aiofiles
        import aiofiles.os

        full = self._resolve(path)
        # 确保父目录存在
        await aiofiles.os.makedirs(full.parent, exist_ok=True)
        # 构建临时文件路径（target.json → target.json.tmp）
        tmp = full.with_suffix(full.suffix + ".tmp")
        try:
            async with aiofiles.open(tmp, "w") as f:
                await f.write(content)
            # 原子替换：reader 要么看到旧文件，要么看到完整新文件，看不到中间态
            await aiofiles.os.replace(tmp, full)
        except Exception:
            # 尽力清理残留的临时文件，避免磁盘堆积
            try:
                await aiofiles.os.remove(tmp)
            except Exception:
                pass
            raise

    async def delete(self, path: str) -> None:
        """删除文件。

        Args:
            path: root 下的相对文件路径。

        Raises:
            StoreError(NOT_FOUND): 文件不存在。
        """
        import aiofiles.os

        full = self._resolve(path)
        try:
            await aiofiles.os.remove(full)
        except FileNotFoundError:
            # 包装为统一的 StoreError，不让调用方依赖系统异常类型
            raise StoreError(StoreErrorCode.NOT_FOUND, f"file {path} not found") from None

    async def list(self, directory: str, pattern: str = "*") -> list[str]:
        """列出目录下的文件，支持 glob 过滤。

        Args:
            directory: root 下的相对目录路径。
            pattern: glob 风格过滤（默认为 "*"）。

        Returns:
            排序后的相对文件路径列表。如果目录不存在则返回空列表（不报错）。
        """
        import aiofiles.os

        dir_path = self._resolve(directory)
        if not await aiofiles.os.path.isdir(dir_path):
            return []
        files: list[str] = []
        async for entry in aiofiles.os.scandir(dir_path):
            # 只返回普通文件（跳过子目录和符号链接）
            if entry.is_file() and Path(entry.name).match(pattern):
                # 返回相对路径，如 "sessions/abc.json" 而非 "/data/memory/sessions/abc.json"
                files.append(str(Path(directory) / entry.name))
        return sorted(files)

    # ══════════════════════════════════════════════════════════════════════
    # 内部方法
    # ══════════════════════════════════════════════════════════════════════

    def _resolve(self, path: str) -> Path:
        """解析相对路径到绝对路径，并强制检查路径在 root 以内。

        这是安全边界 —— 防止目录遍历攻击：
          - path = "../../etc/passwd" → 拒绝
          - path = "sessions/abc.json" → 放行，返回 /data/memory/sessions/abc.json

        Args:
            path: 用户提供的相对路径。

        Returns:
            解析后的绝对路径。

        Raises:
            StoreError(PERMISSION_DENIED): 路径逃逸了 root 目录。
        """
        # resolve() 展开所有 ".." 和符号链接
        full = (self.root / path).resolve()
        # 检查解析后的路径是否仍在 root 前缀以内
        if not str(full).startswith(str(self.root.resolve())):
            raise StoreError(
                StoreErrorCode.PERMISSION_DENIED,
                f"path {path} escapes root directory",
            )
        return full
