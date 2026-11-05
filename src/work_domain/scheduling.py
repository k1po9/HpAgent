"""Pure Work schedule version/enabled/one-shot planning."""

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class SchedulePlan:
    version: int
    enabled: bool
    due_at: datetime | None
    needs_daily_due: bool


def plan_schedule(work, requirement, row) -> SchedulePlan:
    timing = requirement["timing"]
    enabled = work["status"] == "active" and timing["kind"] != "immediate"
    changed = row is None or row["timing"] != timing or row["desired_enabled"] != enabled
    version = (row["schedule_version"] + int(changed)) if row else 1
    due = row["next_due_at"] if row and not changed else None
    needs_daily_due = False
    if enabled and changed:
        if timing["kind"] == "once":
            due = datetime.fromisoformat(timing["due_at"])
        else:
            needs_daily_due = True
        if timing["kind"] == "once" and work["continuation"]["kind"] == "awaiting_delivery":
            due = None
    return SchedulePlan(version, enabled, due, needs_daily_due)
