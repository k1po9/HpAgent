"""Synthetic PG16/PGroonga checks, executed inside the isolated Hindsight image.

Uses the pinned server's coordinator and SQL builders directly. No production
connection defaults; the fixture's mock settings and database prefix are required.
"""
import asyncio
import hashlib
import json
import os
import sys
from urllib.parse import urlsplit
from uuid import uuid4

import asyncpg
from hindsight_api.engine.sql.postgresql import PostgreSQLDialect, knowledge_bm25_arm
from hindsight_api.migrations import ensure_text_search_extension


async def inspect(conn, schema):
    columns = await conn.fetch("""
        SELECT table_name,column_name,data_type,is_generated,generation_expression
        FROM information_schema.columns WHERE table_schema=$1
        AND table_name IN ('memory_units','mental_models')
        ORDER BY table_name,ordinal_position
    """, schema)
    indexes = await conn.fetch("""
        SELECT t.relname AS table_name,i.relname AS index_name,am.amname,
               x.indisvalid,x.indisready,pg_get_indexdef(i.oid) AS definition
        FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid
        JOIN pg_class i ON i.oid=x.indexrelid
        JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_am am ON am.oid=i.relam
        WHERE n.nspname=$1 AND t.relname IN ('memory_units','mental_models')
        ORDER BY t.relname,i.relname
    """, schema)
    return {'columns': [dict(r) for r in columns], 'indexes': [dict(r) for r in indexes]}


async def schema_check(conn, backend):
    data = await inspect(conn, 'public')
    data['server_version'] = await conn.fetchval('SHOW server_version')
    data['extensions'] = [dict(r) for r in await conn.fetch(
        "SELECT extname,extversion FROM pg_extension ORDER BY extname")]
    data['groonga_version'] = await conn.fetchval(
        "SELECT (pgroonga_command('status')::json)->1->>'version'")
    assert data['server_version'].split('.')[0] == '16'
    assert data['groonga_version']
    installed = {r['extname']: r['extversion'] for r in data['extensions']}
    assert installed['vector'] == '0.8.6' and installed['pgroonga'] == '4.0.8'
    for table in ['memory_units', 'mental_models']:
        col = next(c for c in data['columns'] if c['table_name'] == table
                   and c['column_name'] == 'search_vector')
        index = next(i for i in data['indexes']
                     if i['index_name'] == 'idx_' + table + '_text_search')
        assert index['indisvalid'] and index['indisready']
        assert col['data_type'] == ('text' if backend == 'pgroonga' else 'tsvector')
        assert index['amname'] == ('pgroonga' if backend == 'pgroonga' else 'gin')
        if backend == 'pgroonga':
            assert col['is_generated'] == 'NEVER'
            assert 'TokenBigram' in index['definition']
            assert 'NormalizerNFKC150' in index['definition']
            for field in (['text', 'context', 'text_signals'] if table == 'memory_units'
                          else ['name', 'content']):
                assert field in index['definition']
    return data


async def guards(conn, url, backend):
    assert backend == 'native', 'guard source must be the native fixture'
    results = []
    for case in ['empty', 'populated', 'mental']:
        schema = 'p1_guard_' + case + '_' + uuid4().hex[:8]
        await conn.execute(f'CREATE SCHEMA {schema}')
        for table in ['memory_units', 'mental_models']:
            await conn.execute(f'CREATE TABLE {schema}.{table} '
                               f'(LIKE public.{table} INCLUDING DEFAULTS INCLUDING '
                               'GENERATED INCLUDING CONSTRAINTS)')
            await conn.execute(f'CREATE INDEX idx_{table}_text_search ON '
                               f'{schema}.{table} USING gin(search_vector)')
        if case == 'populated':
            await conn.execute(f"INSERT INTO {schema}.memory_units "
                               "(id,bank_id,text,fact_type) VALUES "
                               "(gen_random_uuid(),'p1-guard','synthetic preserved fact','world')")
        if case == 'mental':
            await conn.execute(f"INSERT INTO {schema}.mental_models "
                               "(id,bank_id,name,source_query,content) VALUES "
                               "(gen_random_uuid(),'p1-guard','syntheticname','synthetic query','synthetic preserved model')")
        before = await inspect(conn, schema)
        error = None
        try:
            ensure_text_search_extension(url, 'pgroonga', schema=schema)
        except RuntimeError as exc:
            error = str(exc)
        after = await inspect(conn, schema)
        if case == 'populated':
            assert error and 'memory_units(1 rows)' in error
            assert before == after
            assert await conn.fetchval(f'SELECT count(*) FROM {schema}.memory_units') == 1
        else:
            assert error is None
            assert all(c['data_type'] == 'text' and c['is_generated'] == 'NEVER'
                       for c in after['columns'] if c['column_name'] == 'search_vector')
            assert all(i['amname'] == 'pgroonga' and i['indisvalid']
                       for i in after['indexes'])
            if case == 'mental':
                assert await conn.fetchval(f'SELECT content FROM {schema}.mental_models') == 'synthetic preserved model'
                assert await conn.fetchval(f"SELECT count(*) FROM {schema}.mental_models "
                    "WHERE (COALESCE(name, '') || ' ' || content) &@~ 'syntheticname'") == 1
        results.append({'schema': schema, 'case': case, 'passed': True, 'expected_rejection': error,
                        'before': before, 'after': after})
    return results


def plan_summary(plan):
    keys = ['Node Type', 'Index Name', 'Plan Rows', 'Actual Rows',
            'Actual Total Time', 'Shared Hit Blocks', 'Shared Read Blocks']
    return {**{k: plan[k] for k in keys if k in plan},
            'Plans': [plan_summary(p) for p in plan.get('Plans', [])]}


async def plans(conn, backend):
    schema = 'p1_plans_' + uuid4().hex[:8]
    await conn.execute(f'CREATE SCHEMA {schema}')
    for table in ['memory_units', 'mental_models']:
        await conn.execute(f'CREATE TABLE {schema}.{table} (LIKE public.{table} INCLUDING ALL)')
    reports = []
    for size in [3, 300, 3000]:
        bank = 'p1-plan-' + str(size)
        token = 'needletoken' + str(size)
        await conn.execute(f"""
            INSERT INTO {schema}.memory_units(id,bank_id,text,context,text_signals,fact_type)
            SELECT gen_random_uuid(),$1,
                   CASE WHEN n=1 THEN $2 ELSE 'ordinary filler ' || n::text END,
                   'contextonlytoken', 'signalonlytoken', 'world'
            FROM generate_series(1,$3::int) n
        """, bank, token, size)
        await conn.execute(f"""
            INSERT INTO {schema}.mental_models(id,bank_id,name,source_query,content)
            SELECT gen_random_uuid(),$1,
                   CASE WHEN n=1 THEN $2 ELSE 'ordinary name ' || n::text END,
                   'synthetic query','modelcontenttoken' FROM generate_series(1,$3::int) n
        """, bank, token, size)
    if backend == 'native':
        await conn.execute(f"UPDATE {schema}.memory_units SET search_vector="
                           "to_tsvector('english',COALESCE(text,'') || ' ' || "
                           "COALESCE(context,'') || ' ' || COALESCE(text_signals,''))")
    await conn.execute(f'ANALYZE {schema}.memory_units')
    await conn.execute(f'ANALYZE {schema}.mental_models')
    dialect = PostgreSQLDialect()
    for size in [3, 300, 3000]:
        bank = 'p1-plan-' + str(size)
        for table in ['memory_units', 'mental_models']:
            if table == 'memory_units':
                sql = dialect.build_bm25_arm(table=f'{schema}.memory_units', cols='id',
                    fact_type='world', bank_id_param='$1', limit_param='$2',
                    text_param='$3', text_search_extension=backend)
            else:
                arm = knowledge_bm25_arm(text_search_extension=backend,
                                        table_alias='mm', text_param='$3')
                score = ('pgroonga_score(mm.tableoid, mm.ctid)' if backend == 'pgroonga'
                         else "ts_rank_cd(mm.search_vector,to_tsquery('english',$3))")
                sql = (f'SELECT mm.id,{score} AS bm25_score FROM {schema}.mental_models mm '
                       f'WHERE mm.bank_id=$1 {arm.match_filter} ORDER BY {arm.order_by} LIMIT $2')
            for query in ['needletoken' + str(size),
                          'contextonlytoken' if table == 'memory_units' else 'modelcontenttoken',
                          'signalonlytoken' if table == 'memory_units' else 'ordinary']:
                rows = await conn.fetch(sql, bank, 10, query)
                assert rows, (table, size, query)
                raw = json.loads(await conn.fetchval('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + sql,
                                                     bank, 10, query))[0]
                reports.append({'schema': schema, 'table': table, 'bank_size': size, 'query': query,
                                'sql_from_pinned_dialect': sql, 'results': len(rows),
                                'scores': [r['bm25_score'] for r in rows],
                                'plan': plan_summary(raw['Plan']),
                                'execution_ms': raw['Execution Time'],
                                'enable_seqscan': await conn.fetchval('SHOW enable_seqscan')})
    return reports


async def snapshot(conn):
    # Only the smoke bank is included; all content is synthetic. No memory text is exported.
    data = {}
    for table, key in [('banks', 'bank_id'), ('documents', 'id'), ('memory_units', 'id'),
                       ('mental_models', 'id'), ('async_operations', 'operation_id')]:
        rows = await conn.fetch(f'SELECT {key}::text AS id FROM public.{table} '
                                "WHERE bank_id='hpagent-u-p1-persistence' ORDER BY 1")
        ids = [r['id'] for r in rows]
        data[table] = {'count': len(ids),
                       'ids_sha256': hashlib.sha256(json.dumps(ids).encode()).hexdigest()}
    return data


async def main():
    assert os.getenv('HINDSIGHT_API_LLM_PROVIDER') == 'mock'
    assert os.getenv('HINDSIGHT_API_RERANKER_PROVIDER') == 'rrf'
    url = os.environ['HINDSIGHT_API_DATABASE_URL']
    target = urlsplit(url)
    assert target.hostname == 'postgres' and target.path.startswith('/pgroonga_')
    backend = os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION']
    conn = await asyncpg.connect(url, command_timeout=30)
    try:
        action = sys.argv[1]
        if action == 'schema':
            data = await schema_check(conn, backend)
        elif action == 'guards':
            data = await guards(conn, url, backend)
        elif action == 'plans':
            data = await plans(conn, backend)
        elif action == 'snapshot':
            data = await snapshot(conn)
        else:
            raise ValueError('unknown fixture action')
        print(json.dumps({'action': action, 'backend': backend, 'passed': True, 'data': data}, default=str))
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
