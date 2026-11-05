"""Frozen inputs to Work application use cases."""

from dataclasses import dataclass
from uuid import UUID

from work_domain.models import Requirement


@dataclass(frozen=True)
class AcceptWorkCommand:
    account_id: UUID
    key: str
    title: str
    requirement: Requirement
    conversation_id: UUID | None = None
    source_message_id: UUID | None = None
    workspace_save_requested: bool | None = None


@dataclass(frozen=True)
class AdvanceWorkCommand:
    account_id: UUID
    work_id: UUID
    key: str
    row_version: int
