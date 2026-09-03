"""Deterministic adapter selection by capability, media type, and extension."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from file_domain.models import FileResource


@dataclass(frozen=True)
class _Registration:
    capability: str
    adapter: Any
    media_types: frozenset[str]
    extensions: frozenset[str]
    priority: int


class FileAdapterRegistry:
    def __init__(self) -> None:
        self._registrations: list[_Registration] = []

    def register(
        self,
        capability: str,
        adapter: Any,
        *,
        media_types: tuple[str, ...] = (),
        extensions: tuple[str, ...] = (),
        priority: int = 0,
    ) -> None:
        normalized_extensions = frozenset(
            value.casefold() if value.startswith(".") else f".{value.casefold()}"
            for value in extensions
        )
        registration = _Registration(
            capability,
            adapter,
            frozenset(value.casefold() for value in media_types),
            normalized_extensions,
            priority,
        )
        if registration in self._registrations:
            raise ValueError("adapter registration already exists")
        self._registrations.append(registration)

    def resolve(self, capability: str, resource: FileResource) -> Any:
        extension = resource.local_path.suffix.casefold()
        candidates = [
            registration
            for registration in self._registrations
            if registration.capability == capability
            and (
                resource.media_type.casefold() in registration.media_types
                or extension in registration.extensions
            )
        ]
        if not candidates:
            raise LookupError(
                f"no {capability} adapter for {resource.media_type} ({extension or 'no extension'})"
            )
        candidates.sort(key=lambda item: item.priority, reverse=True)
        if len(candidates) > 1 and candidates[0].priority == candidates[1].priority:
            raise LookupError("ambiguous file adapter registration")
        return candidates[0].adapter
