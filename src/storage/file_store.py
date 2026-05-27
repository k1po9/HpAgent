"""
LocalFileStore —— FileStore 协议的本地文件系统实现。

所有文件 I/O 操作必须通过此实现，禁止直接使用 open()。
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from .protocols import FileStore, StoreError, StoreErrorCode

logger = logging.getLogger(__name__)


class LocalFileStore:
    """FileStore 协议的本地文件系统实现。

    root 目录在构造时指定，所有路径操作均限定在此根目录内，
    防止路径遍历漏洞。

    用法::

        store = LocalFileStore(root=Path(".data/data/sessions"))
        content = await store.read("session-abc.jsonl")
        await store.write("session-abc.jsonl", json_line + "\\n")
        files = await store.list(".", pattern="*.jsonl")
    """

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()

    # ── FileStore 协议实现 ───────────────────────────────────────────

    async def read(self, path: str) -> str:
        """读取文件全部文本内容。

        Raises:
            StoreError(NOT_FOUND): 文件不存在。
            StoreError(PERMISSION_DENIED): 路径尝试逃逸根目录。
        """
        filepath = self._resolve(path)
        try:
            return await asyncio.to_thread(filepath.read_text, encoding="utf-8")
        except FileNotFoundError:
            raise StoreError(StoreErrorCode.NOT_FOUND, f"File not found: {path}")

    async def write(self, path: str, content: str) -> None:
        """写入文件。父目录不存在时自动创建。"""
        filepath = self._resolve(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(filepath.write_text, content, encoding="utf-8")

    async def delete(self, path: str) -> None:
        """删除文件。文件不存在时抛出 StoreError(NOT_FOUND)。"""
        filepath = self._resolve(path)
        try:
            await asyncio.to_thread(filepath.unlink)
        except FileNotFoundError:
            raise StoreError(StoreErrorCode.NOT_FOUND, f"File not found: {path}")

    async def list(self, directory: str, pattern: str = "*") -> list[str]:
        """列出目录下的文件。目录不存在时返回空列表。"""
        dirpath = self._resolve(directory)
        if not dirpath.exists():
            return []
        files = sorted(dirpath.glob(pattern))
        return [str(f.relative_to(self._root)) for f in files if f.is_file()]

    # ── 扩展：同步方法（供 WorkspaceManager 等同步代码使用）─────────

    def read_sync(self, path: str) -> str:
        """同步读取文件全部文本内容。"""
        filepath = self._resolve(path)
        try:
            return filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            raise StoreError(StoreErrorCode.NOT_FOUND, f"File not found: {path}")

    def write_sync(self, path: str, content: str) -> None:
        """同步写入文件。父目录不存在时自动创建。"""
        filepath = self._resolve(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(content, encoding="utf-8")

    def write_atomic_sync(self, path: str, content: str) -> None:
        """同步原子写入（先写 .tmp 再 os.replace）。"""
        filepath = self._resolve(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        tmp = filepath.with_suffix(filepath.suffix + ".tmp")
        try:
            tmp.write_text(content, encoding="utf-8")
            os.replace(tmp, filepath)
        except Exception:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    def exists_sync(self, path: str) -> bool:
        """同步检查文件或目录是否存在。"""
        return self._resolve(path).exists()

    def mkdir_sync(self, path: str) -> None:
        """同步创建目录（含父目录）。"""
        self._resolve(path).mkdir(parents=True, exist_ok=True)

    # ── 扩展：异步方法 ────────────────────────────────────────────────

    async def append_line(self, path: str, line: str) -> None:
        """追加一行到文件末尾（用于 JSONL 等增量写入场景）。"""
        filepath = self._resolve(path)
        filepath.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(_append_line_sync, filepath, line)

    async def exists(self, path: str) -> bool:
        """检查文件或目录是否存在。"""
        return self._resolve(path).exists()

    async def mkdir(self, path: str) -> None:
        """创建目录（含父目录）。"""
        self._resolve(path).mkdir(parents=True, exist_ok=True)

    # ── 内部 ────────────────────────────────────────────────────────

    def _resolve(self, path: str) -> Path:
        """解析相对路径并检查路径遍历。"""
        resolved = (self._root / path).resolve()
        if not str(resolved).startswith(str(self._root)):
            raise StoreError(
                StoreErrorCode.NOT_FOUND,
                f"Path traversal denied: {path}",
            )
        return resolved


def _append_line_sync(filepath: Path, line: str) -> None:
    """同步追加一行到文件（在 asyncio.to_thread 中执行）。"""
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(line)
