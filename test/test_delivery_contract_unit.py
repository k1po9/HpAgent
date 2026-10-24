"""Byte integrity and requirement validation; no provider or external requests."""
import hashlib
from dataclasses import replace
from uuid import uuid4

import pytest

from application.main_response import MANIFEST
from file_runtime.format_check import verify_format
from resources.model_budget_context import current_model_call, model_budget_scope
from resources.model_client import ModelClient
from work_domain.models import Requirement


def requirement(formats=('markdown',), extra=()):
    items, criteria = [], []
    for i, format in enumerate(formats):
        oid, fid = f'output{i}', f'format{i}'
        items.append(dict(id=f'delivery{i}', format=format, required=True,
                          output_criterion_id=oid, format_criterion_id=fid))
        criteria.extend([dict(id=oid, required=True, evidence_types=['output_exists']),
                         dict(id=fid, required=True, evidence_types=['check_passed'])])
    return Requirement('Publish requested formats', 'generic_work', {'schema_version': 1},
        acceptance_criteria=tuple(criteria) + tuple(extra), deliverables={'schema_version': 1, 'items': items})


def test_legacy_hash_contract_is_unchanged_and_new_contract_roundtrips():
    legacy = Requirement('legacy', 'generic_work', {'schema_version': 1})
    assert 'deliverables' not in legacy.to_dict()
    value = requirement(('markdown', 'docx')).to_dict()
    assert Requirement.from_dict(value).to_dict() == value


@pytest.mark.parametrize('change', ['unknown', 'shared', 'wrong_type', 'wrong_required', 'empty'])
def test_invalid_deliverable_bindings_are_rejected(change):
    req = requirement()
    if change == 'unknown':
        req.deliverables['items'][0]['format'] = 'pdf'
    elif change == 'shared':
        req.deliverables['items'][0]['format_criterion_id'] = 'output0'
    elif change == 'wrong_type':
        req = replace(req, acceptance_criteria=({'id': 'output0', 'required': True, 'evidence_types': ['check_passed']}, req.acceptance_criteria[1]))
    elif change == 'wrong_required':
        req.deliverables['items'][0]['required'] = False
    else:
        req.deliverables['items'] = []
    with pytest.raises(ValueError):
        req.to_dict()


def test_byte_checks_reject_renaming_and_hash_mismatch(tmp_path):
    from file_adapters import DocxWriter
    mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
    DocxWriter().create(tmp_path, 'a.docx', [{'kind': 'paragraph', 'text': 'Word document'}])
    docx = (tmp_path / 'a.docx').read_bytes()
    md = '# Markdown\n\nBody'.encode()
    digest = lambda data: hashlib.sha256(data).hexdigest()
    assert verify_format(md, 'markdown', 'text/markdown', 'a.md', digest(md))
    assert verify_format(docx, 'docx', mime, 'a.docx', digest(docx))
    assert not verify_format(docx, 'markdown', 'text/markdown', 'a.md', digest(docx))
    assert not verify_format(md, 'docx', mime, 'a.docx', digest(md))
    assert not verify_format(md, 'markdown', 'text/markdown', 'a.md', '0'*64)
    assert not verify_format(b'\xff', 'markdown', 'text/markdown', 'a.md', digest(b'\xff'))


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_tool_first_constraints_are_frozen_in_request_and_are_task_local(api):
    client = ModelClient({'api_key': 'offline', 'base_url': 'https://example.test/v1', 'model': 'test',
                          'api_format': api, 'extra_body': {'tool_choice': 'auto'}})
    with model_budget_scope(str(uuid4()), str(uuid4()), 'decision'):
        current_model_call().tool_choice_required = True
        body = client.prepare_request([{'role': 'user', 'content': 'Hello'}], tools=[MANIFEST]).body()
        assert body['tool_choice'] == ('required' if api == 'openai' else {'type': 'any', 'disable_parallel_tool_use': True})
        if api == 'openai':
            assert body['parallel_tool_calls'] is False
    assert current_model_call() is None


def test_only_additive_text_definition_upgrade_can_resume_frozen_invocations():
    from copy import deepcopy

    from agent_activities.text_result import compatible_text_definition, text_definition
    current = text_definition().to_dict()
    legacy = deepcopy(current)
    legacy['description'] = 'Legacy Markdown publisher'
    legacy['input_schema']['properties'].pop('format')
    legacy['verifier_keys'] = []
    assert compatible_text_definition(legacy, current)
    changed = deepcopy(current)
    changed['limits']['max_duration_seconds'] += 1
    assert not compatible_text_definition(legacy, changed)
    changed = deepcopy(current)
    changed['implementation_version'] = 'unreviewed'
    assert not compatible_text_definition(legacy, changed)
    changed = deepcopy(legacy)
    changed['input_schema']['properties']['format'] = {'type': 'string'}
    assert not compatible_text_definition(changed, current)
