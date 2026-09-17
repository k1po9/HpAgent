"""Canonical normalization for channel identities."""
from __future__ import annotations

QQ_CHANNELS = frozenset({"napcat", "official_qq"})


def normalize_web_subject(subject: str) -> str:
    return (subject or "").strip().casefold()


def normalize_qq_subject(channel_type: str, subject: str) -> str | None:
    value = (subject or "").strip()
    if channel_type not in QQ_CHANNELS or not value:
        return None
    return f"{channel_type}:{value}"


def normalize_channel_subject(
    channel_type: str, subject: str
) -> tuple[str, str] | None:
    if channel_type == "web":
        normalized = normalize_web_subject(subject)
        return ("web", normalized) if normalized else None
    normalized = normalize_qq_subject(channel_type, subject)
    return ("qq", normalized) if normalized else None
