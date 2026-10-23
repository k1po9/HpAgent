"""Research Work accepts the Retriever hard-source scope contract."""
import pytest


@pytest.mark.parametrize('domains', [
    {'allowed_domains': ['developer.mozilla.org']},
    {'excluded_domains': ['example.com']},
])
def test_research_requirement_accepts_optional_hard_source_scope(domains):
    from research_domain.models import SourceStrategy
    from work_domain.models import Requirement

    requirement = Requirement('HTTP caching', 'research_report', {
        'schema_version': 1, 'source_strategy': domains,
    }).to_dict()
    assert SourceStrategy.from_dict(requirement['spec']['source_strategy']).to_dict().items() >= domains.items()
    with pytest.raises(ValueError, match='invalid source strategy'):
        Requirement('HTTP caching', 'research_report', {
            'schema_version': 1, 'source_strategy': {**domains, 'unknown_field': True},
        }).to_dict()
