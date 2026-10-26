"""Normal-optimizer Chinese probes at three Bank sizes, both indexed tables."""
import asyncio
import json
import os
import sys
from uuid import UUID

import asyncpg
from hindsight_api.engine.sql.postgresql import PostgreSQLDialect, knowledge_bm25_arm

sys.path.insert(0, '/p1-fixture')
from verify import plan_summary


async def main():
    assert os.environ['HINDSIGHT_API_LLM_PROVIDER'] == 'mock'
    backend = os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION']
    url = os.environ['HINDSIGHT_API_DATABASE_URL']
    assert url.split('/')[-1] in {'pgroonga_native', 'pgroonga_candidate'}
    schema = 'p3_plans_' + UUID(sys.argv[1]).hex
    conn = await asyncpg.connect(url, command_timeout=30)
    try:
        await conn.execute(f'CREATE SCHEMA {schema}')
        for table in ['memory_units', 'mental_models']:
            await conn.execute(f'CREATE TABLE {schema}.{table} (LIKE public.{table} INCLUDING ALL)')
        for size in [3, 300, 3000]:
            await conn.execute(f'''INSERT INTO {schema}.memory_units(id,bank_id,text,fact_type)
                SELECT gen_random_uuid(),$1,CASE WHEN n=1 THEN '紫砂茶壶库存' ELSE
                'ordinary archive ' || n::text END,'world' FROM generate_series(1,$2::int) n''',
                str(size), size)
            await conn.execute(f'''INSERT INTO {schema}.mental_models(id,bank_id,name,source_query,content)
                SELECT gen_random_uuid(),$1,'synthetic','fixture',CASE WHEN n=1 THEN
                '紫砂茶壶库存' ELSE 'ordinary archive ' || n::text END
                FROM generate_series(1,$2::int) n''', str(size), size)
        if backend == 'native':
            await conn.execute(f"UPDATE {schema}.memory_units SET search_vector=to_tsvector('english',text)")
        output = []
        for table in ['memory_units', 'mental_models']:
            await conn.execute(f'ANALYZE {schema}.{table}')
            indexes = [dict(r) for r in await conn.fetch('''SELECT i.relname,am.amname,
                pg_relation_size(i.oid) AS bytes FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid
                JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_am am ON am.oid=i.relam
                JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=$1 AND t.relname=$2
                AND am.amname IN ('gin','pgroonga')''', schema, table)]
            for size in [3, 300, 3000]:
                if table == 'memory_units':
                    sql = PostgreSQLDialect().build_bm25_arm(table=f'{schema}.{table}', cols='id',
                        fact_type='world', bank_id_param='$1',limit_param='$2',text_param='$3',
                        text_search_extension=backend)
                else:
                    arm = knowledge_bm25_arm(text_search_extension=backend,table_alias='mm',text_param='$3')
                    score = 'pgroonga_score(mm.tableoid,mm.ctid)' if backend == 'pgroonga' else "ts_rank_cd(mm.search_vector,to_tsquery('english',$3))"
                    sql = f'SELECT mm.id,{score} AS bm25_score FROM {schema}.{table} mm WHERE mm.bank_id=$1 {arm.match_filter} ORDER BY {arm.order_by} LIMIT $2'
                for qid, q in [('partial_zh', '紫砂'), ('full_zh', '紫砂茶壶库存')]:
                    rows = await conn.fetch(sql, str(size), 10, q)
                    plan = json.loads(await conn.fetchval('EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) '+sql,str(size),10,q))[0]
                    output.append(dict(table=table,bank_size=size,query_id=qid,
                        results=len(rows),scores=[r['bm25_score'] for r in rows],
                        plan=plan_summary(plan['Plan']),execution_ms=plan['Execution Time'],
                        enable_seqscan=await conn.fetchval('SHOW enable_seqscan'),indexes=indexes))
        print(json.dumps(dict(backend=backend,schema=schema,probes=output)))
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
