"""Admission checks for newly requested schedules; recovery keeps catch-up."""
from datetime import UTC, datetime

from web_domain.errors import DomainError


class WorkTimeInPast(DomainError, ValueError):
    def __init__(self, due_at: str, now: datetime):
        self.due_at = due_at
        self.server_now = now.isoformat()
        super().__init__(
            f"work_time_in_past: due_at={due_at}; server_now={self.server_now}. "
            "一次性定时必须晚于当前时间；请核对日期和时区，明确过去日期须询问是否现在执行。"
        )


def validate_new_timing(timing: dict, *, now: datetime | None = None) -> None:
    if timing['kind'] != 'once':
        return
    current = now if now is not None else datetime.now(UTC)
    if datetime.fromisoformat(timing['due_at']) <= current:
        raise WorkTimeInPast(timing['due_at'], current)
