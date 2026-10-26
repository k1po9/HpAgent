"""The operations gate rejects distinct configuration/catalog faults without writes."""
from __future__ import annotations

import copy
import json
import subprocess
from unittest.mock import patch

import pytest

from memory.service_readiness import assess_service, inspect_service, normalized_expression


def effective_fixture(backend='pgroonga'):
    target = {'host': 'postgres', 'port': 5432, 'database': 'fixture', 'schema': 'public'}
    expected = {'api_version': '1.1.0', 'image_digest': 'fixed',
                'target': target, 'text_search': {'extension': backend, 'native_language': None}}
    observed = {'api_version': '1.1.0', 'image_digest': 'fixed', 'target': dict(target),
                'text_search': dict(expected['text_search']), 'database': 'fixture', 'server_port': 5432,
                'server_address_matches': True, 'schema_exists': True, 'postgres_major': 16,
                'extensions': {'vector': '0.8.6', 'pgroonga': '4.0.8'},
                'disabled_banks': 0, 'operations': [], 'columns': [], 'indexes': []}
    from memory.service_readiness import DOCUMENTS
    for table, expr in DOCUMENTS.items():
        observed['columns'].append({'table_name': table, 'column_name': 'search_vector',
                                    'data_type': 'text', 'is_generated': 'NEVER'})
        observed['indexes'].append({'table_name': table, 'index_name': f'idx_{table}_text_search',
            'amname': 'pgroonga', 'indisvalid': True, 'indisready': True, 'partial': False,
            'keys': [expr], 'options': ['tokenizer=TokenBigram', 'normalizer=NormalizerNFKC150']})
    return expected, observed


@pytest.mark.parametrize('fault,code', [
    ('database', 'target_mismatch'), ('schema', 'schema_mismatch'), ('backend', 'backend_mismatch'),
    ('extension', 'extension_missing'), ('extension_version', 'extension_version_mismatch'),
    ('pg_major', 'pg_major_mismatch'), ('vector', 'vector_version_mismatch'),
    ('column', 'column_mismatch'), ('index', 'index_missing'), ('invalid', 'index_invalid'),
    ('expression', 'index_expression_mismatch'), ('grouping', 'index_expression_mismatch'),
    ('separator', 'index_expression_mismatch'),
    ('partial', 'index_expression_mismatch'), ('options', 'index_options_mismatch'),
    ('bank', 'bank_text_search_disabled'), ('version', 'version_mismatch'),
    ('image', 'image_mismatch'), ('unknown_operation', 'operations_not_drained'),
])
def test_catalog_fault_matrix(fault, code):
    expected, observed = effective_fixture()
    assert assess_service(expected, observed)['service_ready']
    if fault == 'database': observed['target']['database'] = 'other'
    elif fault == 'schema': observed['schema_exists'] = False
    elif fault == 'backend': observed['text_search']['extension'] = 'native'
    elif fault == 'extension': del observed['extensions']['pgroonga']
    elif fault == 'extension_version': observed['extensions']['pgroonga'] = 'other'
    elif fault == 'pg_major': observed['postgres_major'] = 17
    elif fault == 'vector': observed['extensions']['vector'] = 'other'
    elif fault == 'column': observed['columns'][0]['data_type'] = 'tsvector'
    elif fault == 'index': observed['indexes'].pop(0)
    elif fault == 'invalid': observed['indexes'][0]['indisvalid'] = False
    elif fault == 'expression': observed['indexes'][0]['keys'][0] = "COALESCE(text, '')"
    elif fault == 'grouping': observed['indexes'][0]['keys'][0] = "COALESCE(text, '' || ' ' || COALESCE(context, '') || ' ' || COALESCE(text_signals, ''))"
    elif fault == 'separator': observed['indexes'][0]['keys'][0] = observed['indexes'][0]['keys'][0].replace("' '", "'  '")
    elif fault == 'partial': observed['indexes'][0]['partial'] = True
    elif fault == 'options': observed['indexes'][0]['options'] = ['tokenizer=TokenUnigram']
    elif fault == 'bank': observed['disabled_banks'] = 1
    elif fault == 'version': observed['api_version'] = 'other'
    elif fault == 'image': observed['image_digest'] = 'other'
    elif fault == 'unknown_operation': observed['operations'] = [{'status': 'unknown', 'count': 1}]
    before = copy.deepcopy(observed)
    result = assess_service(expected, observed)
    assert not result['service_ready'] and 'memory_service_' + code in result['problems']
    assert observed == before


@pytest.mark.parametrize('error', ['memory_service_permission_denied', 'memory_service_transport_failed'])
def test_probe_failures_are_safe_and_cannot_become_ready(error):
    responses = [subprocess.CompletedProcess([], 0, json.dumps('fixed')),
                 subprocess.CompletedProcess([], 1, json.dumps({'error_code': error}), 'secret-dsn')]
    with patch('memory.service_readiness.subprocess.run', side_effect=responses):
        result = inspect_service('fixture', effective_fixture()[0])
    assert result == {'service_ready': False, 'problems': [error]}
    assert 'secret-' not in json.dumps(result)


def test_normalization_preserves_literals_and_complete_document():
    assert normalized_expression("(COALESCE(name, ''::character varying)::text || ' '::text || content)") == normalized_expression("COALESCE(name,'') || ' ' || content")
    assert normalized_expression("' '") != normalized_expression("''")
