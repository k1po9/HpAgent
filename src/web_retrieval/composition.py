"""Worker composition; configuration chooses exactly one public-web search provider."""
from research_adapters.deepseek import DeepSeekWebProvider
from research_adapters.discovery import (
    CompositeSourceDiscoveryProvider,
    GitHubDiscoveryProvider,
    RSSDiscoveryProvider,
)
from research_adapters.web import SearXNGDiscoveryProvider
from research_domain.providers import SourceDiscoveryProvider

from .adapters import ResearchRetrievalAdapter
from .budget import paid_before_request
from .safety import SafeLocalFetcher
from .service import WebRetrievalService


def compose_retrieval(config, canonicalizer):
    extras: list[SourceDiscoveryProvider] = [GitHubDiscoveryProvider(), RSSDiscoveryProvider(timeout_seconds=config.search_timeout_seconds, safe_network=True)]
    searches = {'legacy': CompositeSourceDiscoveryProvider([
        SearXNGDiscoveryProvider(config.searxng_url, timeout_seconds=config.search_timeout_seconds), *extras])}
    fetches: dict[str, SafeLocalFetcher | DeepSeekWebProvider] = {'local': SafeLocalFetcher(canonicalizer)}
    if config.deepseek_search_fetch_key:
        cloud = DeepSeekWebProvider(config.deepseek_search_fetch_key, before_request=paid_before_request,
                                   timeout_seconds=config.search_timeout_seconds)
        searches['deepseek'] = CompositeSourceDiscoveryProvider([cloud, *extras])
        fetches['deepseek'] = cloud
    service = WebRetrievalService(searches, fetches, default_search=config.search_provider,
                                  default_fetch=config.fetch_provider)
    return ResearchRetrievalAdapter(service, search_timeout=config.search_timeout_seconds,
                                    fetch_timeout=config.fetch_timeout_seconds)
