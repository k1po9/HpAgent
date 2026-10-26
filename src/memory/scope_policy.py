"""Scopes derived only from persisted/authenticated Chat context, never model input."""

from dataclasses import dataclass
from typing import Mapping


class MemoryScopeError(ValueError):
    """Missing or inconsistent trusted account/scope; no request may be sent."""


@dataclass(frozen=True)
class MemoryScope:
    account_id: str
    tags: tuple[str, ...]

    @property
    def bank_id(self) -> str:
        return f"hpagent-u-{self.account_id}"

    @property
    def observation_scopes(self) -> list[list[str]]:
        return [list(self.tags)]

    def contains(self, tags: list[str] | tuple[str, ...]) -> bool:
        return set(self.tags) == set(tags)

    def matches_document(self, document: Mapping) -> bool:
        scopes = document.get('observation_scopes') or []
        return (self.contains(document.get('tags') or []) and len(scopes) == 1
                and self.contains(scopes[0]))


def memory_scope(account_id: str, scope: str, group_id: str = "") -> MemoryScope:
    if not account_id or account_id != account_id.strip():
        raise MemoryScopeError("memory_account_required")
    if scope == "private" and not group_id:
        return MemoryScope(account_id, ("scope:private",))
    if scope in {"group", "guild"} and group_id and group_id == group_id.strip():
        return MemoryScope(account_id, ("scope:group", f"group:{group_id}"))
    raise MemoryScopeError("memory_scope_required")


def chat_scope(account_id: str, origin: Mapping) -> MemoryScope:
    # Web has no external routing scope; its authenticated Conversation is private.
    scope = origin.get("scope")
    if scope is None and origin.get("channel_type", "web") == "web":
        scope = "private"
    return memory_scope(
        account_id,
        scope or "",
        origin.get("context_key", "") if scope in {"group", "guild"} else "",
    )
