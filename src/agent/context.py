"""Execution context — layered state management with concurrent-safety.

Design decisions (from 5-round architecture review):
  - SessionState + SharedMemory + RuntimeConfig split (not a single global_context dict)
  - SharedMemory provides CAS primitive for race-condition prevention
  - trace_id for full-link observability
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SessionState:
    """User session — conversation history, user identity."""
    user_id: str = ""
    conversation_history: list = field(default_factory=list)


class SharedMemory:
    """Concurrent-safe key-value store shared across agents.

    Supports namespace isolation and atomic CAS operations.
    In asyncio's single-threaded model, dict writes are safe but logical races
    (read-check-write) require CAS.
    """

    def __init__(self) -> None:
        self._namespaces: dict[str, dict] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    async def get(self, namespace: str, key: str) -> Any:
        async with self._lock:
            return self._namespaces.get(namespace, {}).get(key)

    async def set(self, namespace: str, key: str, value: Any) -> None:
        async with self._lock:
            if namespace not in self._namespaces:
                self._namespaces[namespace] = {}
            self._namespaces[namespace][key] = value

    async def compare_and_set(self, namespace: str, key: str, expected: Any, new_value: Any) -> bool:
        """Atomic CAS — prevents race-condition writes."""
        async with self._lock:
            current = self._namespaces.get(namespace, {}).get(key)
            if current == expected:
                if namespace not in self._namespaces:
                    self._namespaces[namespace] = {}
                self._namespaces[namespace][key] = new_value
                return True
            return False

    async def delete(self, namespace: str, key: str) -> bool:
        async with self._lock:
            ns = self._namespaces.get(namespace)
            if ns and key in ns:
                del ns[key]
                return True
            return False

    def snapshot(self) -> dict[str, dict]:
        """Return a shallow copy of all namespaces (not under lock — caller must ensure safety)."""
        return {ns: dict(items) for ns, items in self._namespaces.items()}


@dataclass
class RuntimeConfig:
    """Runtime configuration — timeout, retry, model selection."""
    timeout_seconds: int = 300
    max_retries: int = 3
    model_name: str = "default"


@dataclass
class ExecutionContext:
    """Layered execution context passed through the entire orchestration."""
    session: SessionState = field(default_factory=SessionState)
    shared_memory: SharedMemory = field(default_factory=SharedMemory)
    config: RuntimeConfig = field(default_factory=RuntimeConfig)
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
