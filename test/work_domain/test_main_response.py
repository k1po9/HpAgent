"""Finite response-claim rules; no provider or database is needed."""
import pytest

from application.main_response import (
    _supported_business_claims,
    explicit_business_success_claims,
)


@pytest.mark.parametrize(('answer', 'kind'), [
    ('我已经为你创建了一个后台任务。', 'accept_work'),
    ('后台任务已创建成功。', 'accept_work'),
    ('后台任务已经提交。', 'accept_work'),
    ('已经提交后台任务', 'accept_work'),
    ('我已委托一个研究任务。', 'accept_work'),
    ('工作项建立成功。', 'accept_work'),
    ('我已经把报告保存到工作空间。', 'save_file'),
    ('报告保存成功。', 'save_file'),
    ('报告已保存到工作空间。', 'save_file'),
    ('已经保存这份文档。', 'save_file'),
    ('文件已上传。', 'upload_file'),
    ('文件已删除。', 'delete_file'),
    ('文档更新成功。', 'update_file'),
    ('报告已发布。', 'publish_file'),
    ('报告生成成功。', 'create_file'),
    ('任务已修改。', 'revise_work'),
    ('后台任务已暂停。', 'pause_work'),
    ('任务恢复成功。', 'resume_work'),
    ('我已经停止任务。', 'stop_work'),
    ('工作项已推进。', 'advance_work'),
    ('任务关联成功。', 'link_work'),
    ('任务已完成。', 'complete_work'),
    ('我已发送消息。', 'send_message'),
    ('通知发送成功。', 'send_message'),
    ('I created the task.', 'accept_work'),
    ('The report has been saved.', 'save_file'),
    ('I paused the work.', 'pause_work'),
    ('I sent the message.', 'send_message'),
    ('我已经创建了“后台任务”。', 'accept_work'),
    ('“报告”保存成功。', 'save_file'),
])
def test_explicit_business_success_claim(answer, kind):
    assert explicit_business_success_claims(answer) == {kind}


@pytest.mark.parametrize('answer', [
    '根据已保存的记忆，地点是桂雨楼。',
    '我记得你之前去过桂雨楼。',
    '我没有保存这份文件。',
    '我没有成功保存文件。',
    '报告尚未保存成功。',
    '我无法确认后台任务已经提交。',
    '如果任务创建成功，系统会通知你。',
    '假如报告保存成功，请通知我。',
    '用户提到了‘任务已提交’这句话。',
    '用户提到任务已经提交。',
    '你之前说“报告保存成功”。',
    '“任务已提交”这句话描述的是示例。',
    '示例：“任务已提交”。',
    '任务已提交了吗？',
    '是否已创建任务？',
    'I have not created the task.',
    'If I saved the file, I would tell you.',
])
def test_non_assertions_are_not_business_success_claims(answer):
    assert explicit_business_success_claims(answer) == set()


@pytest.mark.parametrize(('answer', 'kinds'), [
    ('后台任务已经提交。报告保存成功。', {'accept_work', 'save_file'}),
    ('我没有保存文件，但后台任务已创建成功。', {'accept_work'}),
    ('如果任务创建成功，系统会通知你。报告保存成功。', {'save_file'}),
    ('用户提到了“任务已提交”这句话，我已经把报告保存到工作空间。', {'save_file'}),
    ('用户提到了“任务已提交”这句话，但我已经创建了“后台任务”。', {'accept_work'}),
    ('用户说“任务已提交”后我已经保存报告。', {'save_file'}),
    ('用户说“任务已提交”而我已保存“报告”。', {'save_file'}),
    ('“报告”已保存，用户说“任务已提交”。', {'save_file'}),
])
def test_non_assertion_does_not_hide_an_independent_claim(answer, kinds):
    assert explicit_business_success_claims(answer) == kinds


@pytest.mark.parametrize(('operation', 'status', 'supported'), [
    ('accept_work', 'active', True),
    ('revise_work', 'active', True),
    ('link_work', 'active', True),
    ('pause_work', 'paused', True),
    ('pause_work', 'pausing', False),
    ('resume_work', 'active', True),
    ('stop_work', 'stopped', True),
    ('stop_work', 'stopping', False),
    ('complete_work', 'completed', False),
    ('save_file', 'active', False),
    ('advance_work', 'active', False),
])
def test_receipt_operation_and_effect_are_both_required(operation, status, supported):
    body = {'work': {'work_id': 'work', 'status': status}}
    assert _supported_business_claims(operation, body) == ({operation} if supported else set())


def test_advancement_requires_an_admitted_run_and_work_identity():
    body = {'work': {'work_id': 'work', 'status': 'active'}, 'run': {'run_id': 'run'}}
    assert _supported_business_claims('advance_work', body) == {'advance_work'}
    assert _supported_business_claims('accept_work', {}) == set()
