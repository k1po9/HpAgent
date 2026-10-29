"""Timeout notices preserve failure facts and use configured, frozen text."""
from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from application.prompts import PromptLoader
from conversation_domain.delivery import enqueue_qq_timeout
from conversation_domain.run_projection import project_terminal
from delivery.service import DeliveryTargetUnavailable
from orchestration.config import PromptsConfig


def test_timeout_reply_loaded_from_prompt_config(tmp_path):
    (tmp_path / 'system.yaml').write_text('qq_model_timeout_reply: 自定义超时提示\n')
    loader = PromptLoader(PromptsConfig.from_dir(tmp_path))
    assert loader.get_system('qq_model_timeout_reply') == '自定义超时提示'
    shipped = PromptLoader(PromptsConfig.from_dir(Path('config/prompts')))
    assert shipped.get_system('qq_model_timeout_reply').strip()


@pytest.mark.parametrize(('status', 'code', 'reply', 'notify'), [
    ('failed', 'model_read_timeout', '配置提示', True),
    ('failed', 'model_connection_failed', '配置提示', False),
    ('cancelled', 'model_read_timeout', '配置提示', False),
    ('failed', 'model_read_timeout', '  ', False),
])
def test_only_final_timeout_enqueues_notice(status, code, reply, notify):
    uow = MagicMock()
    run = {'run_id': uuid4(), 'account_id': uuid4(), 'conversation_id': uuid4(), 'failure_code': code}
    with patch('conversation_domain.run_projection.MessageRepository') as messages, \
         patch('conversation_domain.run_projection.OutboxRepository') as outbox, \
         patch('conversation_domain.delivery.enqueue_qq_timeout') as enqueue:
        project_terminal(uow, run, status, qq_timeout_reply=reply)
        messages.return_value.set_terminal.assert_called_once_with(
            uow, run['run_id'], 'failed' if status == 'failed' else 'aborted', None,
        )
        assert enqueue.call_count == int(notify)
        if notify:
            enqueue.assert_called_once_with(uow, run['run_id'], reply)
        # No successful-reply memory retention is scheduled.
        assert outbox.return_value.enqueue.call_count == 1
        assert outbox.return_value.enqueue.call_args.args[3] == 'publish_terminal_event'


@pytest.mark.parametrize('stage', ['target', 'enqueue'])
def test_unavailable_target_rolls_back_only_the_optional_notice(stage):
    uow = MagicMock()
    run_id = uuid4()
    uow.execute.return_value.fetchone.return_value = {
        'account_id': uuid4(), 'origin': {'scope': 'private'},
    }
    savepoint = uow.connection.transaction.return_value
    with patch('delivery.service.target_from_origin') as target, \
         patch('delivery.service.enqueue') as enqueue:
        (target if stage == 'target' else enqueue).side_effect = DeliveryTargetUnavailable('revoked')
        enqueue_qq_timeout(uow, run_id, 'notice')
        assert savepoint.__exit__.call_args.args[0] is DeliveryTargetUnavailable
        assert enqueue.call_count == int(stage == 'enqueue')


def test_timeout_notice_does_not_hide_unexpected_failures():
    uow = MagicMock()
    uow.execute.return_value.fetchone.return_value = {
        'account_id': uuid4(), 'origin': {'scope': 'private'},
    }
    with patch('delivery.service.target_from_origin', side_effect=ValueError('invalid route')):
        with pytest.raises(ValueError, match='invalid route'):
            enqueue_qq_timeout(uow, uuid4(), 'notice')
