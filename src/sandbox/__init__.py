"""
Sandbox — the hands layer.

All external operations (tool execution, channel I/O) are proxied through sandboxes.
"""
from .sandbox import Sandbox
from .sandbox_manager import SandboxManager

__all__ = ["Sandbox", "SandboxManager"]
