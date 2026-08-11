"""Small metrics snapshot boundary used by the scheduled Activity."""
from __future__ import annotations

from typing import Any


class MetricsSnapshotService:
    def __init__(self, memory_service: Any) -> None:
        self._memory = memory_service

    async def snapshot(self) -> dict[str, Any]:
        return await self._memory.get_metrics()
