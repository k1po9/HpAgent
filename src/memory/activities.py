"""Instance-owned Activities for scheduled long-term memory maintenance."""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, cast

from temporalio import activity


class ScheduledMemoryActivities:
    def __init__(self, *, memory_reflection: Any, metrics: Any) -> None:
        self._memory_reflection = memory_reflection
        self._metrics = metrics

    @activity.defn(name="reflect_activity")
    async def reflect(self, account_id: str) -> Dict[str, Any]:
        return cast(Dict[str, Any], await self._memory_reflection.reflect(account_id))

    @activity.defn(name="reflect_batch_activity")
    async def reflect_batch(self, account_ids: List[str]) -> Dict[str, Any]:
        return cast(
            Dict[str, Any], await self._memory_reflection.reflect_batch(account_ids)
        )

    @activity.defn(name="metrics_report_activity")
    async def metrics_report(self) -> Dict[str, Any]:
        snapshot = await self._metrics.snapshot()
        logging.getLogger("HpAgent.Metrics").info(
            "HindsightMetrics|%s",
            json.dumps(snapshot, ensure_ascii=False, default=str),
        )
        return cast(Dict[str, Any], snapshot)
