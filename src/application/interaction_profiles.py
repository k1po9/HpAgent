"""Surface-selected interaction profiles consumed by the Context capability."""
from __future__ import annotations

from typing import Mapping

WEB_CHAT = "web_chat"
WEB_PLAN = "web_plan"
QQ_PRIVATE = "qq_private"
QQ_GROUP = "qq_group"
CONSOLE = "console"

INTERACTION_PROFILES = frozenset({WEB_CHAT, WEB_PLAN, QQ_PRIVATE, QQ_GROUP, CONSOLE})

_IDENTITY_KEYS = {
    WEB_CHAT: "web",
    WEB_PLAN: "web",
    QQ_PRIVATE: "napcat",
    QQ_GROUP: "napcat",
    CONSOLE: "console",
}


def validate_interaction_profile(value: str) -> str:
    if value not in INTERACTION_PROFILES:
        raise ValueError(f"unsupported interaction profile: {value}")
    return value


def identity_key_for_profile(profile: str) -> str:
    """Map a semantic profile to the existing prompt key without transport inference."""
    return _IDENTITY_KEYS[validate_interaction_profile(profile)]


def select_interaction_profile(
    origin: Mapping[str, object] | None,
    agent_strategy: str = "react",
) -> str:
    """Select a profile at the Chat source boundary.

    New surface adapters persist ``interaction_profile``. The transport mapping is
    retained here only for Runs accepted before W4-B.
    """
    source = dict(origin or {})
    explicit = str(source.get("interaction_profile") or "")
    if explicit:
        return validate_interaction_profile(explicit)
    if source.get("channel_type") in {"napcat", "official_qq"}:
        return QQ_GROUP if source.get("scope") in {"group", "guild"} else QQ_PRIVATE
    return WEB_PLAN if agent_strategy == "plan_and_execute" else WEB_CHAT


def interaction_source(origin: Mapping[str, object] | None) -> str:
    """Return source-provided display metadata; it never selects Agent behavior."""
    source = dict(origin or {})
    return str(source.get("channel_type") or "web")
