"""Load frozen synthetic facts, never Retain/extract independently in each arm."""
import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import asyncpg


def identity(session, key):
    return uuid5(NAMESPACE_URL, 'hpagent-pgroonga-p3/' + session + '/' + key)


async def main():
    assert os.environ['HINDSIGHT_API_LLM_PROVIDER'] == 'mock'
    assert os.environ['HINDSIGHT_API_RERANKER_PROVIDER'] == 'rrf'
    url = os.environ['HINDSIGHT_API_DATABASE_URL']
    assert url.split('/')[-1] in {'pgroonga_native', 'pgroonga_candidate'}
    session = sys.argv[1]
    data = json.loads(Path(__file__).with_name('dataset.json').read_text())
    conn = await asyncpg.connect(url, command_timeout=30)
    stamp = '2026-10-25T00:00:00+00:00'
    banks = {a: 'hpagent-u-' + str(identity(session, a)) for a in ['main', 'other']}
    config = dict(enable_observations=True, enable_auto_consolidation=False,
                  enable_text_search=True, enable_temporal_retrieval=True,
                  enable_graph_retrieval=True, enable_reranking=True)
    try:
        async with conn.transaction():
            for bank in banks.values():
                # Unique session identity; never delete or overwrite existing test data.
                await conn.execute('INSERT INTO banks(bank_id,config) VALUES($1,$2::jsonb)',
                                   bank, json.dumps(config))
            for d in data['documents']:
                bank = banks[d['account']]
                await conn.execute('INSERT INTO documents(id,bank_id,original_text,tags) VALUES($1,$2,$3,$4)',
                                   d['document_id'], bank, d['text'], d['tags'])
                await conn.execute('''INSERT INTO memory_units
                    (id,bank_id,document_id,text,embedding,tags,fact_type,event_date,
                     occurred_start,occurred_end,mentioned_at,created_at,updated_at,consolidated_at)
                    VALUES($1,$2,$3,$4,$5::vector,$6,'world',$7::text::timestamptz,
                           $7::text::timestamptz,$7::text::timestamptz,$7::text::timestamptz,
                           $7::text::timestamptz,$7::text::timestamptz,$7::text::timestamptz)''',
                    identity(session, d['document_id']), bank, d['document_id'], d['text'],
                    json.dumps([1.0] + [0.0] * 383), d['tags'], stamp)
            for o in data['observations']:
                await conn.execute('''INSERT INTO memory_units
                    (id,bank_id,text,embedding,tags,fact_type,source_memory_ids,
                     observation_scopes,consolidated_at,created_at,updated_at)
                    VALUES($1,$2,$3,$4::vector,$5,'observation',$6,$7::jsonb,
                           $8::text::timestamptz,$8::text::timestamptz,$8::text::timestamptz)''',
                    identity(session, o['fact_id']), banks['main'], o['text'],
                    json.dumps([1.0] + [0.0] * 383), o['tags'],
                    [identity(session, x) for x in o['sources']], json.dumps([o['tags']]), stamp)
            if os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'] == 'native':
                await conn.execute("UPDATE memory_units SET search_vector=to_tsvector('english',text) "
                                   'WHERE bank_id=ANY($1::text[])', list(banks.values()))
        await conn.execute('ANALYZE memory_units')
        rows = await conn.fetch('''SELECT id::text,bank_id,document_id,text,embedding::text,tags,
            fact_type,source_memory_ids::text FROM memory_units
            WHERE bank_id=ANY($1::text[]) ORDER BY id''', list(banks.values()))
        canonical = json.dumps([dict(r) for r in rows], sort_keys=True)
        assert len(rows) == 310
        print(json.dumps(dict(fact_count=len(rows),document_count=300,
                              facts_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
                              bank_config=config, banks=banks, session=session)))
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
