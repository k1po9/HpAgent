from typing import Dict, List, Optional, Any
from threading import RLock
import uuid
import time
from .sandbox import Sandbox
from .tools.base import BaseTool
from common.errors import SandboxNotFoundError


class SandboxManager:
    def __init__(self, max_idle_seconds: int = 300):
        self._sandboxes: Dict[str, Sandbox] = {}
        self._lock = RLock()
        self._max_idle_seconds = max_idle_seconds

    def create_sandbox(self, tools: Optional[List[BaseTool]] = None, resources: Optional[Dict[str, Any]] = None, sandbox_id: Optional[str] = None) -> str:
        with self._lock:
            sandbox = Sandbox(sandbox_id=sandbox_id or str(uuid.uuid4()), tools=tools, resources=resources)
            self._sandboxes[sandbox.sandbox_id] = sandbox
            return sandbox.sandbox_id

    def get_sandbox(self, sandbox_id: str) -> Sandbox:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if not sandbox:
                raise SandboxNotFoundError(sandbox_id)
            return sandbox

    def destroy_sandbox(self, sandbox_id: str) -> bool:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
            if not sandbox:
                return False
            sandbox.destroy()
            del self._sandboxes[sandbox_id]
            return True

    def list_sandboxes(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [sandbox.get_info() for sandbox in self._sandboxes.values()]

    def cleanup_idle_sandboxes(self) -> int:
        with self._lock:
            current_time = time.time()
            to_destroy = []
            for sandbox_id, sandbox in self._sandboxes.items():
                if current_time - sandbox.last_used > self._max_idle_seconds:
                    to_destroy.append(sandbox_id)
            for sandbox_id in to_destroy:
                self._sandboxes[sandbox_id].destroy()
                del self._sandboxes[sandbox_id]
            return len(to_destroy)

    def get_sandbox_count(self) -> int:
        with self._lock:
            return len(self._sandboxes)

    def health_check_all(self) -> Dict[str, bool]:
        with self._lock:
            return {sandbox_id: sandbox.health_check() for sandbox_id, sandbox in self._sandboxes.items()}
