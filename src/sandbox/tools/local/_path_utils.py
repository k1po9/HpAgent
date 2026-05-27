import os


def safe_resolve(workspace_root: str, user_path: str) -> str:
    """Resolve user_path relative to workspace_root, blocking traversal."""
    full = os.path.normpath(os.path.join(workspace_root, user_path))
    if not full.startswith(os.path.normpath(workspace_root)):
        raise ValueError(f"Path traversal blocked: {user_path}")
    return full


def safe_cwd(workspace_root: str, cwd: str) -> str:
    """Resolve a cwd for Bash, ensuring it stays within workspace."""
    if not cwd:
        return workspace_root
    full = os.path.normpath(os.path.join(workspace_root, cwd))
    if not full.startswith(os.path.normpath(workspace_root)):
        raise ValueError(f"cwd escapes workspace: {cwd}")
    return full


def make_relative(workspace_root: str, abs_path: str) -> str:
    """Convert an absolute path under workspace_root to relative."""
    return os.path.relpath(abs_path, workspace_root)
