"""The editor, command writer and Generic executor share executable contract fixtures."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from work_domain.commands import WorkCommandService
from work_domain.execution_result import generic_result
from work_domain.models import (
    GENERIC_RESULT_EVIDENCE_TYPES,
    LegacyGenericCriteria,
    Requirement,
    validate_new_completion_criteria,
)

CONTRACT = json.loads((Path(__file__).parent / 'fixtures/work/generic_completion.json').read_text())


def test_editor_fixture_is_accepted_by_the_command_and_evidence_contracts():
    from agent_activities.result_tools import discover

    value = Requirement.from_dict(CONTRACT['new_requirement']).to_dict()
    validate_new_completion_criteria(value)
    assert value == CONTRACT['new_requirement']
    assert sorted(GENERIC_RESULT_EVIDENCE_TYPES) == CONTRACT['result_evidence_types']
    tool = next(t['function'] for t in discover(model_facing=False)
                if t['function']['name'] == 'result__evidence')
    kinds = tool['parameters']['properties']['predicate']['properties']['kind']['enum']
    assert set(kinds) == GENERIC_RESULT_EVIDENCE_TYPES


def test_legacy_read_and_explicit_revision_keep_the_full_requirement():
    legacy = {**CONTRACT['new_requirement'], 'acceptance_criteria': CONTRACT['legacy_criteria']}
    # Parsing and returning an immutable old requirement must not rewrite it.
    parsed = Requirement.from_dict(legacy).to_dict()
    assert parsed == legacy
    with pytest.raises(LegacyGenericCriteria):
        validate_new_completion_criteria(parsed)
    upgraded = {**parsed, 'acceptance_criteria': CONTRACT['upgraded_criteria']}
    validate_new_completion_criteria(upgraded)
    assert {k: v for k, v in upgraded.items() if k != 'acceptance_criteria'} == {
        k: v for k, v in legacy.items() if k != 'acceptance_criteria'
    }


@pytest.mark.parametrize('types', [['operation_receipt'], ['operation_receipt', 'output_exists']])
def test_new_generic_writes_reject_legacy_even_in_mixed_or_optional_criteria(types):
    for required in (True, False):
        value = {**CONTRACT['new_requirement'], 'acceptance_criteria': [
            {'id': 'result', 'required': required, 'evidence_types': types}]}
        with pytest.raises(LegacyGenericCriteria):
            validate_new_completion_criteria(value)


def test_legacy_receipt_cannot_bypass_typed_result_validation():
    content = json.dumps({'schema_version': 1, 'kind': 'deliverable_ready', 'evidence': [
        {'criterion_id': 'result', 'type': 'operation_receipt', 'ref': 'successful-tool'}]})
    with pytest.raises(ValueError, match='typed result evidence'):
        generic_result(None, {}, {}, content, 'final-result')


@pytest.mark.parametrize('operation', ['accept', 'revise'])
def test_original_legacy_command_replay_is_returned_before_new_write_validation(operation):
    legacy = Requirement.from_dict({**CONTRACT['new_requirement'],
                                    'acceptance_criteria': CONTRACT['legacy_criteria']})
    commands = WorkCommandService('unused')
    receipt = Mock()
    with patch('work_domain.commands.UnitOfWork'), patch.object(
        commands, '_claim', return_value=(receipt, None)
    ), patch('work_domain.commands.validate_new_completion_criteria') as validate:
        if operation == 'accept':
            result = commands.accept('account', 'key', 'Title', legacy)
        else:
            result = commands.revise('account', 'work', 'key', 1, legacy)
    assert result is receipt
    validate.assert_not_called()
