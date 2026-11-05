"""Durable Web Artifacts derived from completed assistant messages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .services import ArtifactService

__all__ = ["ArtifactService"]


def __getattr__(name: str) -> type[ArtifactService]:
    # Queries are also imported by application commands. Avoid eagerly loading
    # their compatibility facade while those commands are still being defined.
    if name == "ArtifactService":
        from .services import ArtifactService

        return ArtifactService
    raise AttributeError(name)
