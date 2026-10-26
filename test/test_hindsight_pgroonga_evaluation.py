"""Frozen Observation provenance against explicitly isolated P3 Banks."""
import json
import os
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
from hindsight_client import Hindsight
from hindsight_client_api.models.recall_request import RecallRequest

pytestmark = pytest.mark.skipif(
    os.environ.get('HINDSIGHT_CONTRACT_ISOLATED') != '1',
    reason='requires dedicated synthetic P3 Banks and explicit opt-in',
)


async def test_frozen_observation_source_sets():
    session = os.environ['PGROONGA_EVALUATION_SESSION']
    url = os.environ['HINDSIGHT_CONTRACT_URL']
    assert url == 'http://hindsight:8888'
    fixture = json.loads(Path('test/fixtures/hindsight-pgroonga/evaluation/dataset.json').read_text())

    def identity(key):
        return str(uuid5(NAMESPACE_URL, 'hpagent-pgroonga-p3/' + session + '/' + key))

    sdk = Hindsight(url, max_attempts=1)
    try:
        result = (await sdk.memory.recall_memories('hpagent-u-' + identity('main'),
            RecallRequest(query=fixture['queries'][0]['frozen_rewrite'], budget='mid',
                max_tokens=4096, tags=['scope:private'], tags_match='all_strict',
                query_timestamp='2026-10-25T00:00:00+00:00',
                include={'source_facts': {'max_tokens': 4096}}))).to_dict()
        observations = {r['id']: r for r in result['results'] if r['type'] == 'observation'}
        report = []
        for o in fixture['observations']:
            row = observations[identity(o['fact_id'])]
            expected = {identity(d) for d in o['sources']}
            assert set(row['source_fact_ids']) == expected
            present = expected & set(result['source_facts'])
            assert len(present) == (2 if 'missing-source' not in o['sources'] else 1)
            for fact_id in present:
                source = result['source_facts'][fact_id]
                assert source['tags'] == ['scope:private']
                assert identity(source['document_id']) == fact_id
            report.append(dict(fact_id=row['id'], source_fact_ids=sorted(expected),
                               returned_source_fact_ids=sorted(present)))
        output = os.environ.get('PGROONGA_EVALUATION_EVIDENCE')
        if output:
            Path(output).write_text(json.dumps(report, indent=2) + '\n')
    finally:
        await sdk.aclose()
