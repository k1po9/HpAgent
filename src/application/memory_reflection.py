"""Scheduled long-term memory reflection service."""
from __future__ import annotations

from typing import Any


class MemoryReflectionService:
    def __init__(self, memory_service: Any) -> None:
        self._memory = memory_service

    async def reflect(self, account_id: str) -> dict[str, int]:
        return {"insights": await self._memory.reflect(account_id)}

    async def reflect_batch(self, account_ids: list[str]) -> dict[str, Any]:
        results: dict[str, int] = {}
        for account_id in account_ids:
            try:
                result = await self.reflect(account_id)
                results[account_id] = result["insights"]
            except Exception:
                results[account_id] = -1
        return {"results": results, "total": len(account_ids)}
