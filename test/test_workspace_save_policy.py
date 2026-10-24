"""Save intent is explicit; new-write checks never rewrite historical requirements."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from application.main_agent import MAIN_ROLE_CONTEXT, create_main_work_tools
from work_domain.commands import WorkCommandService
from work_domain.models import Requirement
from work_domain.save_policy import (
    InvalidWorkspaceSave,
    required_save_criterion,
    requires_workspace_save,
    validate_new_save_policy,
)


def requirement(*, save=False, directory=None):
    criteria = [{'id': 'text', 'required': True, 'evidence_types': ['output_exists']}]
    if save:
        criteria.append({'id': 'save', 'required': True, 'evidence_types': ['workspace_saved']})
    return Requirement('Publish Markdown', 'generic_work', {'schema_version': 1},
        acceptance_criteria=tuple(criteria), deliverable_policy={
            'schema_version': 1, 'required': bool(directory),
            **({'directory_id': directory} if directory else {}),
        }).to_dict()


def test_ordinary_output_and_explicit_unresolved_save_have_distinct_contracts():
    ordinary, unresolved = requirement(), requirement(save=True)
    validate_new_save_policy(ordinary, workspace_save_requested=False)
    validate_new_save_policy(unresolved, workspace_save_requested=True)
    assert not requires_workspace_save(ordinary)
    assert requires_workspace_save(unresolved)
    with pytest.raises(InvalidWorkspaceSave, match='Ordinary published/downloadable'):
        validate_new_save_policy(unresolved, workspace_save_requested=False)
    with pytest.raises(InvalidWorkspaceSave, match='workspace_saved'):
        validate_new_save_policy(ordinary, workspace_save_requested=True)


def test_alternative_evidence_does_not_make_workspace_saving_mandatory():
    value = requirement()
    value['acceptance_criteria'][0]['evidence_types'].append('workspace_saved')
    assert not required_save_criterion(value)
    assert not requires_workspace_save(value)
    validate_new_save_policy(value, workspace_save_requested=False)
    with pytest.raises(InvalidWorkspaceSave):
        validate_new_save_policy(value, workspace_save_requested=True)
    # A mandatory destination independently requires saving; an existing mixed
    # criterion can register the exact save evidence without being rewritten.
    value['deliverable_policy'] = {'schema_version': 1, 'required': True, 'directory_id': str(uuid4())}
    assert requires_workspace_save(value)
    validate_new_save_policy(value, workspace_save_requested=True)


@pytest.mark.parametrize('field', ['directory_id', 'entry_id'])
def test_zero_uuid_remains_readable_but_new_writes_reject_it(field):
    value = requirement(save=True)
    value['deliverable_policy'][field] = '00000000-0000-0000-0000-000000000000'
    assert Requirement.from_dict(value).to_dict() == value
    with pytest.raises(InvalidWorkspaceSave, match='zero UUID'):
        validate_new_save_policy(value)


@pytest.mark.parametrize('operation', ['accept', 'revise'])
def test_old_save_command_replay_precedes_new_intent_and_destination_checks(operation):
    old = requirement(save=True)
    old['deliverable_policy']['directory_id'] = '00000000-0000-0000-0000-000000000000'
    commands, receipt = WorkCommandService('unused'), Mock()
    with patch('work_domain.commands.UnitOfWork'), patch.object(
        commands, '_claim', return_value=(receipt, None)
    ), patch('work_domain.commands.validate_new_save_policy') as validate:
        if operation == 'accept':
            result = commands.accept('account', 'key', 'Title', Requirement.from_dict(old),
                                     workspace_save_requested=False)
        else:
            result = commands.revise('account', 'work', 'key', 1, Requirement.from_dict(old),
                                     workspace_save_requested=False)
    assert result is receipt
    validate.assert_not_called()


async def test_main_defaults_to_publication_and_passes_the_explicit_save_opt_in():
    commands = SimpleNamespace(accept=Mock(return_value=SimpleNamespace(body={'work': {'work_id': str(uuid4())}})))
    tools = {t.name: t for t in create_main_work_tools({
        'account_id': str(uuid4()), 'conversation_id': str(uuid4()), 'trigger_message_id': str(uuid4()),
    }, commands)}
    await tools['accept_work'].ainvoke({'title': 'Text', 'mandate_slot': 'text', 'requirement': requirement()})
    assert commands.accept.call_args.kwargs['workspace_save_requested'] is False
    for name in ('accept_work', 'revise_work'):
        flag = tools[name].args_schema['properties']['workspace_save_requested']
        assert flag['default'] is False and flag['type'] == 'boolean'
    assert '生成、发布或下载 Markdown 不等于保存到 Workspace' in MAIN_ROLE_CONTEXT
