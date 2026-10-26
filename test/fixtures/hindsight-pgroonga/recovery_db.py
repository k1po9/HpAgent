"""Synthetic restore fixture and read-only whole-row digests; no row text emitted."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sys
from uuid import UUID, uuid4

import asyncpg
from hindsight_api.engine.sql.postgresql import PostgreSQLDialect, knowledge_bm25_arm
from verify import plan_summary, schema_check


async def main():
    assert os.environ['HINDSIGHT_API_LLM_PROVIDER'] == 'mock'
    url = os.environ['HINDSIGHT_API_DATABASE_URL']
    assert url.split('/')[-1] in {'pgroonga_native', 'pgroonga_candidate', 'p4_native_restore', 'p4_candidate_restore'}
    action, account = sys.argv[1:3]
    bank = 'hpagent-u-' + str(UUID(account))
    conn = await asyncpg.connect(url, command_timeout=30)
    try:
        if action == 'decorate':
            fact = await conn.fetchval("SELECT id FROM memory_units WHERE bank_id=$1 AND fact_type='world' ORDER BY id LIMIT 1", bank)
            assert fact
            obs, entity = uuid4(), uuid4()
            await conn.execute('''INSERT INTO memory_units(id,bank_id,text,embedding,tags,fact_type,
                source_memory_ids,observation_scopes,consolidated_at)
                SELECT $1,bank_id,'p4needle 紫砂茶壶 synthetic observation',embedding,tags,'observation',
                ARRAY[id],jsonb_build_array(to_jsonb(tags)),now() FROM memory_units WHERE id=$2''', obs, fact)
            await conn.execute("INSERT INTO entities(id,bank_id,canonical_name) VALUES($1,$2,'P4 synthetic entity')", entity, bank)
            await conn.execute('INSERT INTO unit_entities(unit_id,entity_id) VALUES($1,$2)', obs, entity)
            await conn.execute("INSERT INTO memory_links(from_unit_id,to_unit_id,link_type,bank_id,entity_id) VALUES($1,$2,'entity',$3,$4)", fact, obs, bank, entity)
            await conn.execute('''INSERT INTO mental_models(id,bank_id,name,source_query,content,embedding,tags)
                SELECT $1,bank_id,'p4model 紫砂茶壶','synthetic p4','p4needle 紫砂茶壶 model',embedding,tags
                FROM memory_units WHERE id=$2''', str(uuid4()), fact)
            if os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'] == 'native':
                await conn.execute("UPDATE memory_units SET search_vector=to_tsvector('english',text) WHERE id=$1", obs)
        assert action in {'decorate','snapshot'}
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            tables = ['banks','documents','chunks','memory_units','invalidated_memory_units',
                      'mental_models','async_operations','entities','memory_links']
            output = {}
            for table in tables:
                rows = await conn.fetch(f'SELECT to_jsonb(t)::text AS row FROM public.{table} t WHERE bank_id=$1', bank)
                # Same-backend restores must preserve ALL columns, including vectors/times/metadata.
                digest = hashlib.sha256('\n'.join(sorted(r['row'] for r in rows)).encode()).hexdigest()
                output[table] = dict(count=len(rows), sha256=digest)
            rows = await conn.fetch('''SELECT to_jsonb(u)::text AS row FROM unit_entities u
                JOIN memory_units m ON m.id=u.unit_id WHERE m.bank_id=$1''', bank)
            output['unit_entities'] = dict(count=len(rows), sha256=hashlib.sha256('\n'.join(sorted(r['row'] for r in rows)).encode()).hexdigest())
            schema = await schema_check(conn, os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'])
            # Plan/score probes use restored, live tables with default optimizer settings.
            if os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'] == 'pgroonga':
                scores = await conn.fetch("SELECT pgroonga_score(tableoid,ctid) AS score FROM memory_units WHERE bank_id=$1 AND (COALESCE(text,'')||' '||COALESCE(context,'')||' '||COALESCE(text_signals,'')) &@~ 'p4needle'", bank)
            else:
                scores = await conn.fetch("SELECT ts_rank_cd(search_vector,to_tsquery('english','p4needle')) AS score FROM memory_units WHERE bank_id=$1 AND search_vector @@ to_tsquery('english','p4needle')", bank)
            assert scores
            probes = []
            backend = os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION']
            for table in ['memory_units', 'mental_models']:
                if table == 'memory_units':
                    sql = PostgreSQLDialect().build_bm25_arm(table='public.memory_units',cols='id',
                        fact_type='observation',bank_id_param='$1',limit_param='$2',text_param='$3',
                        text_search_extension=backend)
                else:
                    arm = knowledge_bm25_arm(text_search_extension=backend,table_alias='mm',text_param='$3')
                    score = 'pgroonga_score(mm.tableoid,mm.ctid)' if backend == 'pgroonga' else "ts_rank_cd(mm.search_vector,to_tsquery('english',$3))"
                    sql = f'SELECT mm.id,{score} AS bm25_score FROM public.mental_models mm WHERE mm.bank_id=$1 {arm.match_filter} ORDER BY {arm.order_by} LIMIT $2'
                rows = await conn.fetch(sql, bank, 10, 'p4needle')
                raw = json.loads(await conn.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql, bank, 10, 'p4needle'))[0]
                assert rows
                probes.append(dict(table=table,results=len(rows),scores=[r['bm25_score'] for r in rows],
                                   plan=plan_summary(raw['Plan'])))
            print(json.dumps(dict(bank=bank,tables=output,schema=schema,probes=probes,
                scores=[r['score'] for r in scores],enable_seqscan=await conn.fetchval('SHOW enable_seqscan')),default=str))
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
