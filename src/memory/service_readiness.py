"""Explicit operations-only inspection of a running Hindsight service.

Import is pure. Docker/process/DB access is invoked only by the operations CLI;
Chat/Worker roles receive neither a Docker handle nor a memory administrator DSN.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
from pathlib import Path
from typing import Any


def normalized_expression(value: str) -> str:
    # Ignore display casts/spacing, preserving function and operator grouping.
    tokens = re.findall(r"'(?:''|[^'])*'|::|[A-Za-z_]\w*|[^\s]", value)
    result = []
    i = 0
    while i < len(tokens):
        if tokens[i:i + 2] == ['::', 'text']:
            i += 2
        elif tokens[i:i + 3] == ['::', 'character', 'varying']:
            i += 3
        else:
            token = tokens[i]
            result.append(token if token.startswith("'") else token.lower())
            i += 1
    while result and result[0] == '(' and result[-1] == ')':
        depth = 0
        for position, token in enumerate(result):
            depth += (token == '(') - (token == ')')
            if depth == 0:
                break
        if position != len(result) - 1:
            break
        result = result[1:-1]
    return ''.join(result)


DOCUMENTS = {
    # Canonical PG16 catalog forms of the pinned Hindsight expressions.
    'memory_units': "((((COALESCE(text, ''::text) || ' '::text) || COALESCE(context, ''::text)) || ' '::text) || COALESCE(text_signals, ''::text))",
    'mental_models': "((COALESCE(name, ''::character varying)::text || ' '::text) || content)",
}
NATIVE_MENTAL_PROJECTION = "to_tsvector('english'::regconfig, (((COALESCE(name, ''::character varying))::text || ' '::text) || content))"


def assess_service(expected: dict[str, Any], observed: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    def reject(code, condition):
        if condition and code not in problems:
            problems.append(code)
    reject('memory_service_version_mismatch', observed['api_version'] != expected['api_version'])
    reject('memory_service_image_mismatch', observed['image_digest'] != expected['image_digest'])
    for field in ['host', 'port', 'database']:
        reject('memory_service_target_mismatch', observed['target'][field] != expected['target'][field])
    reject('memory_service_schema_mismatch', observed['target']['schema'] != expected['target']['schema'])
    reject('memory_service_target_mismatch', observed['database'] != observed['target']['database']
           or observed['server_port'] != observed['target']['port'] or not observed['server_address_matches'])
    reject('memory_service_schema_mismatch', not observed['schema_exists'])
    reject('memory_service_backend_mismatch', observed['text_search'] != expected['text_search'])
    reject('memory_service_pg_major_mismatch', observed['postgres_major'] != 16)
    extensions = observed['extensions']
    reject('memory_service_vector_version_mismatch', extensions.get('vector') != '0.8.6')
    backend = expected['text_search']['extension']
    reject('memory_service_extension_missing', backend == 'pgroonga' and 'pgroonga' not in extensions)
    reject('memory_service_extension_version_mismatch', backend == 'pgroonga'
           and 'pgroonga' in extensions and extensions['pgroonga'] != '4.0.8')
    for table in DOCUMENTS:
        col = next((c for c in observed['columns']
                    if c['table_name'] == table and c['column_name'] == 'search_vector'), None)
        generated = 'ALWAYS' if backend == 'native' and table == 'mental_models' else 'NEVER'
        reject('memory_service_column_mismatch', not col or col['data_type'] != (
            'text' if backend == 'pgroonga' else 'tsvector') or col['is_generated'] != generated)
        if backend == 'native' and table == 'mental_models' and col:
            reject('memory_service_column_expression_mismatch', normalized_expression(
                col['generation_expression'] or '') != normalized_expression(NATIVE_MENTAL_PROJECTION))
        index = next((i for i in observed['indexes']
                      if i['table_name'] == table and i['index_name'] == f'idx_{table}_text_search'), None)
        reject('memory_service_index_missing', index is None)
        if not index:
            continue
        reject('memory_service_index_invalid', not index['indisvalid'] or not index['indisready'])
        reject('memory_service_index_expression_mismatch', index['amname'] != (
            'pgroonga' if backend == 'pgroonga' else 'gin') or index['partial']
            or len(index['keys']) != 1 or normalized_expression(index['keys'][0]) !=
            normalized_expression(DOCUMENTS[table] if backend == 'pgroonga' else 'search_vector'))
        if backend == 'pgroonga':
            reject('memory_service_index_options_mismatch', set(index['options'] or []) !=
                   {'tokenizer=TokenBigram', 'normalizer=NormalizerNFKC150'})
    reject('memory_service_bank_text_search_disabled', observed['disabled_banks'] > 0)
    reject('memory_service_operations_not_drained', any(
        r['count'] and r['status'] not in {'completed', 'cancelled'} for r in observed['operations']))
    return {'service_ready': not problems, 'problems': problems,
            'expected': expected, 'observed': observed, 'business_switch_executed': False}


async def collect_service() -> dict[str, Any]:
    """Run inside the selected service container; all DB statements are read-only."""
    import hashlib
    import importlib.metadata
    import socket
    from urllib.parse import urlsplit

    import asyncpg

    processes = [p for p in Path('/proc').glob('[0-9]*')
                 if (p / 'comm').read_text().strip() == 'hindsight-api']
    if len(processes) != 1:
        raise ValueError('process_identity')
    environment = dict(s.split('=', 1) for s in (processes[0] / 'environ').read_bytes().decode().split('\0') if '=' in s)
    url = environment['HINDSIGHT_API_DATABASE_URL']
    parsed = urlsplit(url)
    schema = environment.get('HINDSIGHT_API_DATABASE_SCHEMA') or 'public'
    if not re.fullmatch(r'[a-z_][a-z0-9_]*', schema):
        raise ValueError('schema_identity')
    backend = environment.get('HINDSIGHT_API_TEXT_SEARCH_EXTENSION') or 'native'
    settings = {'extension': backend, 'native_language': (
        environment.get('HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE') or 'english')
        if backend == 'native' else None}
    conn = await asyncpg.connect(url, timeout=5, server_settings={
        'default_transaction_read_only': 'on', 'statement_timeout': '10000',
        'lock_timeout': '2000', 'search_path': 'pg_catalog,public',
        'application_name': 'hpagent-memory-service-check'})
    try:
        async with conn.transaction(readonly=True, isolation='repeatable_read'):
            identity = dict(await conn.fetchrow("""SELECT current_database() AS database,
                inet_server_addr()::text AS address,inet_server_port() AS server_port,
                current_setting('server_version_num')::int/10000 AS postgres_major,
                current_setting('transaction_read_only') AS read_only"""))
            target = {'host': parsed.hostname, 'port': parsed.port or 5432,
                      'database': parsed.path.lstrip('/'), 'schema': schema}
            schema_exists = await conn.fetchval('SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=$1)', schema)
            columns = [dict(r) for r in await conn.fetch("""SELECT table_name,column_name,
                data_type,is_generated,generation_expression FROM information_schema.columns
                WHERE table_schema=$1 AND table_name IN ('memory_units','mental_models')
                ORDER BY table_name,ordinal_position""", schema)]
            indexes = [dict(r) for r in await conn.fetch("""SELECT t.relname AS table_name,
                i.relname AS index_name,am.amname,x.indisvalid,x.indisready,
                x.indpred IS NOT NULL AS partial,i.reloptions AS options,
                ARRAY(SELECT pg_get_indexdef(i.oid,k,true) FROM generate_series(1,x.indnkeyatts) k) AS keys
                FROM pg_index x JOIN pg_class t ON t.oid=x.indrelid
                JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=t.relnamespace
                JOIN pg_am am ON am.oid=i.relam WHERE n.nspname=$1
                AND t.relname IN ('memory_units','mental_models') ORDER BY t.relname,i.relname""", schema)]
            extensions = {r['extname']: r['extversion'] for r in await conn.fetch('SELECT extname,extversion FROM pg_extension')}
            banks = operations = []
            if schema_exists:
                banks = await conn.fetch(f"SELECT config->'enable_text_search' AS enabled FROM \"{schema}\".banks")
                operations = [dict(r) for r in await conn.fetch(
                    f'SELECT status,count(*) AS count FROM "{schema}".async_operations GROUP BY status ORDER BY status')]
            default_enabled = environment.get('HINDSIGHT_API_ENABLE_TEXT_SEARCH', 'true').lower() in {'true', '1', 'yes'}
            disabled = 0
            for bank in banks:
                # Read only the switch; never include mission/config text in output.
                enabled = json.loads(bank['enabled']) if isinstance(bank['enabled'], str) else bank['enabled']
                if (default_enabled if enabled is None else enabled) is not True:
                    disabled += 1
            addresses = {r[4][0] for r in socket.getaddrinfo(parsed.hostname, parsed.port or 5432)}
            return {'api_version': importlib.metadata.version('hindsight-api-slim'),
                    'target': target, 'text_search': settings, 'database': identity['database'],
                    'server_port': identity['server_port'], 'postgres_major': identity['postgres_major'],
                    'server_address_matches': identity['address'].split('/')[0] in addresses,
                    'schema_exists': schema_exists, 'read_only': identity['read_only'],
                    'extensions': extensions, 'columns': columns, 'indexes': indexes,
                    'bank_count': len(banks), 'disabled_banks': disabled, 'operations': operations,
                    'target_fingerprint': hashlib.sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()}
    finally:
        await conn.close()


def api_target_matches(container: str, api_url: str) -> bool:
    import socket
    from urllib.parse import urlsplit

    parsed = urlsplit(api_url)
    network = subprocess.run(['docker', 'inspect', '--format', '{{json .NetworkSettings}}', container],
                             check=True, capture_output=True, text=True, timeout=5)
    info = json.loads(network.stdout)
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    addresses = {r[4][0] for r in socket.getaddrinfo(parsed.hostname, port)}
    if port == 8888 and addresses.intersection(
        n['IPAddress'] for n in info['Networks'].values() if n.get('IPAddress')
    ):
        return True
    return any(port == int(binding['HostPort']) and addresses.intersection(
        {'127.0.0.1', '::1'} if binding['HostIp'] in {'0.0.0.0', '::'} else {binding['HostIp']}
    ) for binding in info.get('Ports', {}).get('8888/tcp') or [])


def inspect_service(container: str, expected: dict[str, Any], *, api_url: str | None = None) -> dict[str, Any]:
    """Explicit Docker authority belongs to this operator call only."""
    try:
        if api_url is not None and not api_target_matches(container, api_url):
            return {'service_ready': False, 'problems': ['memory_service_api_target_mismatch']}
        image = subprocess.run(['docker', 'inspect', '--format', '{{json .Image}}', container],
                               check=True, capture_output=True, text=True, timeout=5)
        probe = subprocess.run(['docker', 'exec', '-i', container, '/app/api/.venv/bin/python', '-'],
                               input=Path(__file__).read_text(), capture_output=True, text=True, timeout=35)
        if probe.returncode:
            failure = json.loads(probe.stdout)
            return {'service_ready': False, 'problems': [failure['error_code']]}
        observed = json.loads(probe.stdout)
        observed['image_digest'] = json.loads(image.stdout)
        return assess_service(expected, observed)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError) as exc:
        denied = (isinstance(exc, PermissionError) or
                  isinstance(exc, subprocess.CalledProcessError) and
                  'permission denied' in (exc.stderr or '').lower())
        return {'service_ready': False, 'problems': [
            'memory_service_permission_denied' if denied else 'memory_service_transport_failed']}


if __name__ == '__main__':
    try:
        print(json.dumps(asyncio.run(asyncio.wait_for(collect_service(), timeout=25))))
    except Exception as exc:
        name = type(exc).__name__
        code = ('memory_service_permission_denied' if name == 'InsufficientPrivilegeError'
                else 'memory_service_schema_mismatch' if name == 'UndefinedTableError'
                else 'memory_service_target_mismatch' if name == 'InvalidCatalogNameError'
                else 'memory_service_transport_failed')
        print(json.dumps({'error_code': code}))
        raise SystemExit(1)
