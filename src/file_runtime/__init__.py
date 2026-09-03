"""File Assistant resource resolution and adapter selection."""

from .output import OutputPublisher, PublishedOutput
from .registry import FileAdapterRegistry
from .resolver import FileResourceResolver

__all__ = [
    "FileAdapterRegistry", "FileResourceResolver", "OutputPublisher", "PublishedOutput"
]
