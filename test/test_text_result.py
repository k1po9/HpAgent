"""Literal text publication contract rejects authority fields and empty output."""

import pytest
from jsonschema import Draft202012Validator, ValidationError

from agent_activities.composition import compose_capabilities
from agent_activities.text_result import text_definition


def test_text_is_work_only_and_requires_file_service():
    caps = compose_capabilities(object(), services=frozenset({'text'}))
    assert 'capability__text__v1' in {t['function']['name'] for t in caps.discover('work')}
    assert caps.discover('chat') == []
    assert compose_capabilities(object(), services=frozenset()).discover('work') == []
    definition = text_definition().to_dict()
    assert definition['limits']['max_model_calls'] == 0
    validator = Draft202012Validator(definition['input_schema'])
    validator.validate({'title': '简短结果', 'body': '正文 https://example.org'})
    for invalid in ({'title': 'x', 'body': ' '}, {'title': ' ', 'body': 'x'},
                    {'title': 'x', 'body': 'x', 'account_id': 'untrusted'},
                    {'title': 'x', 'body': 'x' * 16001}):
        with pytest.raises(ValidationError):
            validator.validate(invalid)
