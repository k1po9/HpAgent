"""Channel-neutral execution segment and durable wait control contracts."""

from dataclasses import dataclass, field
from typing import Literal

LIFECYCLE_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class SegmentInput:
    schema_version: int
    run_id: str
    account_id: str
    segment_id: str
    execution_id: str = field(kw_only=True)


@dataclass(frozen=True)
class SegmentLease:
    acquired: bool
    fencing_token: int = 0


@dataclass(frozen=True)
class WaitInput:
    schema_version: int
    run_id: str
    account_id: str
    wait_id: str
    operation_id: str
    reason: str
    resume_ref: str
    deadline: str
    execution_id: str = field(kw_only=True)


@dataclass(frozen=True)
class FinishWaitInput:
    wait: WaitInput
    state: Literal["resumed", "cancelled", "expired"]
