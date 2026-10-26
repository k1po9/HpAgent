"""Read-only reconciliation of one P3 session's frozen facts and resource sizes."""
import asyncio
import hashlib
import json
import os
import sys

import asyncpg
from seed import identity


async def main():
    url = os.environ['HINDSIGHT_API_DATABASE_URL']
    assert url.split('/')[-1] in {'pgroonga_native', 'pgroonga_candidate'}
    banks = ['hpagent-u-' + str(identity(sys.argv[1], a)) for a in ['main', 'other']]
    conn = await asyncpg.connect(url, command_timeout=30)
    try:
        async with conn.transaction(readonly=True):
            rows = await conn.fetch('''SELECT id::text,bank_id,document_id,text,embedding::text,tags,
                fact_type,source_memory_ids::text FROM memory_units
                WHERE bank_id=ANY($1::text[]) ORDER BY id''', banks)
            canonical = json.dumps([dict(r) for r in rows], sort_keys=True)
            counts = {}
            for table in ['documents', 'memory_units', 'mental_models', 'async_operations']:
                counts[table] = await conn.fetchval(
                    f'SELECT count(*) FROM {table} WHERE bank_id=ANY($1::text[])', banks)
            sizes = [dict(r) for r in await conn.fetch('''SELECT t.relname AS table_name,
                i.relname AS index_name,i.oid AS index_oid,pg_relation_size(i.oid) AS postgres_relation_bytes
                FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid
                JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname='public' AND i.relname IN
                ('idx_memory_units_text_search','idx_mental_models_text_search')''')]
            if os.environ['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'] == 'pgroonga':
                tables = json.loads(await conn.fetchval("SELECT pgroonga_command('table_list')"))[1][1:]
                for index in sizes:
                    oid = str(index['index_oid'])
                    names = [r[1] for r in tables if r[1] == 'Sources' + oid or r[1].startswith('Lexicon' + oid + '_')]
                    components = []
                    for name in names:
                        inspected = json.loads(await conn.fetchval('SELECT pgroonga_command($1)', 'object_inspect ' + name))[1]
                        components.append(dict(object=name, disk_usage=inspected['disk_usage']))
                        columns = json.loads(await conn.fetchval('SELECT pgroonga_command($1)', 'column_list ' + name))[1][1:]
                        for column in columns:
                            if column[1] == '_key':
                                continue
                            full = name + '.' + column[1]
                            inspected = json.loads(await conn.fetchval('SELECT pgroonga_command($1)', 'object_inspect ' + full))[1]
                            components.append(dict(object=full, disk_usage=inspected['disk_usage']))
                    index['groonga_components'] = components
                    index['groonga_disk_usage_bytes'] = sum(c['disk_usage'] for c in components)
            print(json.dumps(dict(counts=counts, facts_sha256=hashlib.sha256(canonical.encode()).hexdigest(),
                                  indexes=sizes, read_only=await conn.fetchval('SHOW transaction_read_only'))))
    finally:
        await conn.close()


if __name__ == '__main__':
    asyncio.run(main())
