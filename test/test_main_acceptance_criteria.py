"""Main rejects an unsatisfiable required save before claiming an idempotency key."""
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from application.main_agent import create_main_work_tools


async def test_main_required_save_needs_a_typed_save_criterion_before_accept():
    commands = SimpleNamespace(accept=Mock())
    tools = {t.name: t for t in create_main_work_tools({
        'account_id': str(uuid4()), 'conversation_id': str(uuid4()),
        'trigger_message_id': str(uuid4()),
    }, commands)}
    with pytest.raises(ValueError, match='workspace_saved'):
        await tools['accept_work'].ainvoke({'title': 'Save', 'mandate_slot': 'goal', 'requirement': {
            'objective': 'Save the result', 'capability_key': 'generic_work', 'spec': {'schema_version': 1},
            'deliverable_policy': {'schema_version': 1, 'required': True, 'directory_id': str(uuid4())},
        }})
    commands.accept.assert_not_called()


async def test_main_can_record_required_save_before_user_selects_a_directory():
    import json

    wid = str(uuid4())
    commands = SimpleNamespace(accept=Mock(return_value=SimpleNamespace(body={'work': {'work_id': wid}})))
    tools = {t.name: t for t in create_main_work_tools({
        'account_id': str(uuid4()), 'conversation_id': str(uuid4()),
        'trigger_message_id': str(uuid4()),
    }, commands)}
    criterion = {'id': 'save', 'required': True, 'evidence_types': ['workspace_saved']}
    result = json.loads(await tools['accept_work'].ainvoke({
        'title': 'Save after directory selection', 'mandate_slot': 'goal', 'requirement': {
            'objective': 'Research and save the HTML in my chosen directory',
            'capability_key': 'generic_work', 'spec': {'schema_version': 1},
            'acceptance_criteria': [criterion],
            'deliverable_policy': {'schema_version': 1, 'required': False},
        },
    }))
    parsed = commands.accept.call_args.args[3]
    assert list(parsed.acceptance_criteria) == [criterion]
    assert parsed.deliverable_policy == {'schema_version': 1, 'required': False}
    assert result['handoff']['work_id'] == wid


@pytest.mark.parametrize("wrapper", ["{}", "```json\n{}\n```", "Evidence registered.\n\n```json\n{}\n```\n"])
def test_explicit_final_json_wrapping_preserves_typed_contract(wrapper):
    import json

    from application.work_context import _generic_result_object

    result = {"schema_version": 1, "kind": "deliverable_ready", "evidence": [
        {"criterion_id": "saved", "type": "workspace_saved", "ref": str(uuid4())}]}
    assert _generic_result_object(wrapper.format(json.dumps(result))) == result


@pytest.mark.parametrize("content", [None, "Saved successfully", "before {\"kind\":\"deliverable_ready\"}",
    "```json\n{}\n```\nmore prose", "```json\n{}\n```\n```json\n{}\n```", "```json\ninvalid\n```",
    "```python\n{}\n```", "```json\n{}\n```\n```html\n<html/>\n```"])
def test_narrative_or_ambiguous_fences_do_not_supply_completion(content):
    from application.work_context import _generic_result_object

    assert _generic_result_object(content) is None


@pytest.mark.parametrize("patch, expected", [
    ({'schema_version': 1}, 'no top-level schema_version'),
    ({'deliverable_policy': {'schema_version': 1, 'required': True, 'directory_id': None}}, 'keep a required workspace_saved'),
])
async def test_main_rejected_arguments_give_field_specific_feedback_without_acceptance(patch, expected):
    commands = SimpleNamespace(accept=Mock())
    tools = {t.name: t for t in create_main_work_tools({
        'account_id': str(uuid4()), 'conversation_id': str(uuid4()), 'trigger_message_id': str(uuid4()),
    }, commands)}
    requirement = {'objective': 'Research and save', 'capability_key': 'generic_work',
                   'spec': {'schema_version': 1}, **patch}
    with pytest.raises(ValueError, match=expected):
        await tools['accept_work'].ainvoke({'title': 'Research', 'mandate_slot': 'goal', 'requirement': requirement})
    commands.accept.assert_not_called()


def test_accept_and_revise_publish_the_same_nested_requirement_contract():
    from jsonschema import Draft202012Validator
    tools = {t.name: t for t in create_main_work_tools({
        'account_id': str(uuid4()), 'conversation_id': str(uuid4()), 'trigger_message_id': str(uuid4()),
    }, SimpleNamespace())}
    for name in ('accept_work', 'revise_work'):
        schema = tools[name].args_schema['properties']['requirement']
        assert schema['additionalProperties'] is False
        assert 'schema_version' not in schema['properties']
        assert schema['properties']['spec']['properties']['schema_version']['const'] == 1
        assert 'directory_id' in schema['properties']['deliverable_policy']['properties']
        Draft202012Validator(schema).validate({'objective': 'Research and save', 'capability_key': 'generic_work', 'spec': {'schema_version': 1}})
