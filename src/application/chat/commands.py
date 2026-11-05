"""Frozen Chat command inputs; no HTTP or snapshot encoding changes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid5


@dataclass(frozen=True)
class SendMessageCommand:
    account_id: UUID
    conversation_id: UUID
    key: str
    content: str
    agent_strategy: str = "react"
    file_ids: tuple[UUID, ...] = ()
    fixed_html: dict[str, Any] | None = None


def message_request_id(key: str) -> UUID:
    """Map a stable surface key to the shared Message deduplication identity.

    Web supplies its validated UUID. Other adapters supply a namespaced stable
    key derived from provider/bot/room/thread/message identity (at most 128 chars).
    Account ownership remains part of the PG unique constraint. This function
    neither resolves identity nor binds different Conversations together.
    """
    if not key or len(key) > 128:
        raise ValueError("message key must contain between 1 and 128 characters")
    try:
        return UUID(key)
    except ValueError:
        return uuid5(UUID("a8381adf-1d37-5d1b-9b21-3946f56bfd48"), key)
