"""Frozen input for shared Run cancellation."""

from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class CancelRunCommand:
    account_id: UUID
    run_id: UUID
    key: str
