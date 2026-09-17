import os
import stat
from pathlib import Path


def safe_resolve(workspace_root: str, user_path: str) -> str:
    """Resolve an existing or prospective path beneath ``workspace_root``.

    The former string-prefix check treated ``/work-evil`` as a child of
    ``/work`` and followed symlinks.  Resolve the nearest existing parent and
    reject every symlink component before returning a capability-scoped path.
    Callers must still open files with ``O_NOFOLLOW`` when the platform offers
    it to narrow the check/open race.
    """
    if not isinstance(user_path, str) or not user_path or "\x00" in user_path:
        raise ValueError("path must be a non-empty relative path")
    candidate = Path(user_path)
    if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
        raise ValueError(f"Path traversal blocked: {user_path}")

    root = Path(workspace_root).resolve(strict=True)
    current = root
    for part in candidate.parts:
        if part in {"", "."}:
            continue
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise ValueError(f"Symbolic links are not allowed: {user_path}")
            resolved = current.resolve(strict=True)
            if not resolved.is_relative_to(root):
                raise ValueError(f"Path traversal blocked: {user_path}")
            current = resolved

    normalized = current.absolute()
    if not normalized.is_relative_to(root):
        raise ValueError(f"Path traversal blocked: {user_path}")
    return str(normalized)


def safe_cwd(workspace_root: str, cwd: str) -> str:
    """Resolve a cwd for Bash, ensuring it stays within workspace."""
    full = safe_resolve(workspace_root, cwd or ".")
    mode = os.stat(full, follow_symlinks=False).st_mode
    if not stat.S_ISDIR(mode):
        raise ValueError(f"cwd is not a directory: {cwd}")
    return full


def make_relative(workspace_root: str, abs_path: str) -> str:
    """Convert an absolute path under workspace_root to relative."""
    return os.path.relpath(abs_path, workspace_root)
