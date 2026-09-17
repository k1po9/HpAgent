"""Hindsight maintenance adapter for reflection and metrics."""

class HindsightMaintenance:
    """Scheduled long-term capabilities; no short-term Session authority."""
    def __init__(self, hindsight):
        self.hindsight = hindsight

    async def reflect(self, account_id: str) -> int:
        return await self.hindsight.reflect(account_id) if self.hindsight else 0

    async def get_metrics(self) -> dict:
        return self.hindsight.get_metrics() if self.hindsight else {}
