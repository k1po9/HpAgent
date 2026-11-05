"""Pure decisions over incrementally loaded Work admission facts."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class WorkAdmissionFacts:
    progress_block: str | None = None
    budget_blocked: bool = False
    save_ready: bool | None = None
    capacity_available: bool | None = None
    cumulative_budget_exhausted: bool = False


@dataclass(frozen=True)
class WorkAdmissionDecision:
    outcome: Literal["admit", "defer", "reject"]
    reason: str | None = None


def decide_work_admission(facts: WorkAdmissionFacts) -> WorkAdmissionDecision:
    # Facts arrive in original I/O order. Do not eagerly query a later gate.
    if facts.progress_block or facts.budget_blocked:
        return WorkAdmissionDecision("defer", facts.progress_block or "budget_exhausted")
    if facts.save_ready is False:
        return WorkAdmissionDecision("defer", "workspace_save_wait")
    if facts.capacity_available is False:
        return WorkAdmissionDecision("defer", "waiting_capacity")
    if facts.cumulative_budget_exhausted:
        return WorkAdmissionDecision("reject", "budget_exhausted")
    return WorkAdmissionDecision("admit")


def cumulative_budget_exhausted(budget) -> bool:
    return any(
        budget["used"].get(k, 0) + budget["reserved"].get(k, 0) >= limit
        for k, limit in budget["limits"].items()
    )
