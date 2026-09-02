"""Concrete open-source adapters for Research providers."""

from .discovery import (
    CompositeSourceDiscoveryProvider,
    GitHubDiscoveryProvider,
    RSSDiscoveryProvider,
)
from .synthesis import ResourcePoolResearchSynthesizer
from .web import (
    PlaywrightBrowserFetchProvider,
    SearXNGDiscoveryProvider,
    StaticWebContentProvider,
    W3libSourceCanonicalizer,
)

__all__ = [
    "PlaywrightBrowserFetchProvider",
    "SearXNGDiscoveryProvider",
    "StaticWebContentProvider",
    "W3libSourceCanonicalizer",
    "CompositeSourceDiscoveryProvider",
    "GitHubDiscoveryProvider",
    "RSSDiscoveryProvider",
    "ResourcePoolResearchSynthesizer",
]
