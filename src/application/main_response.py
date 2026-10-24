"""Main's terminal action. Its receipt proves a response, never a business command."""
import json
from uuid import UUID

from persistence.uow import UnitOfWork

RESPOND = 'respond_directly'
MANIFEST = {'type': 'function', 'function': {
    'name': RESPOND,
    'description': '结束当前交互并直接回答。普通回答使用 answer；操作成功说明必须引用真实命令 command_refs。回答结束回执不证明任何业务操作成功。需要委托时先调用 accept_work，收到真实回执后再回答。',
    'parameters': {'type': 'object', 'additionalProperties': False,
        'required': ['answer'], 'properties': {
            'answer': {'type': 'string', 'minLength': 1, 'maxLength': 20000},
            'command_refs': {'type': 'array', 'maxItems': 10, 'uniqueItems': True,
                             'items': {'type': 'string', 'format': 'uuid'}}}}}}


def verified_command(uow, account, operation, key):
    row = uow.execute(
        "SELECT idempotency_command_id,operation,response_body FROM idempotency_commands "
        "WHERE account_id=%s AND operation=%s AND idempotency_key=%s AND status='completed' "
        "AND response_status BETWEEN 200 AND 299", (account, operation, key),
    ).fetchone()
    if not row:
        raise ValueError('No verified successful business command')
    return {'command_id': str(row['idempotency_command_id']), 'operation': row['operation']}


def execute_response(store, request, decision):
    from jsonschema.exceptions import ValidationError

    from agent_workflows.contracts import ModelDecisionResult

    try:
        return _execute_response(store, request, decision)
    except (ValidationError, ValueError) as exc:
        operation = request.operation_id + ':respond'
        prior = store.begin_operation(operation, request.run_id, 'tool')
        if prior is not None:
            return ModelDecisionResult(**{**prior['decision'], 'tool_calls': ()})
        ref = 'main-response-error:' + operation
        result = {'schema_version': request.schema_version, 'operation_id': operation,
            'decision_type': 'tool_calls', 'decision_ref': ref, 'tool_calls': (),
            'transcript_version': decision['transcript_version'] + 1,
            'stop_reason': 'invalid_response_action', 'display_summary': '回答动作无效，请修正参数或先执行真实命令。'}
        version = store.complete_operation_with_event(transcript_id=request.transcript_id,
            expected_version=decision['transcript_version'], event_type='tool_result', operation_id=operation,
            event_payload={'message': {'role': 'tool', 'tool_call_id': decision['tool_calls'][0]['tool_call_id'],
                'name': RESPOND, 'content': '回答动作未获接受：' + str(exc)[:300]}},
            result_ref=ref, result_payload={'decision': result})
        result['transcript_version'] = version
        return ModelDecisionResult(**result)


def _execute_response(store, request, decision):
    """Idempotently pair the genuine tool call/result, then provide a terminal result ref."""
    from jsonschema import Draft202012Validator, FormatChecker

    from agent_workflows.contracts import ModelDecisionResult

    call = decision['tool_calls'][0]
    call_id = call['tool_call_id']
    arguments = decision['tool_call_arguments'][call_id]
    Draft202012Validator(MANIFEST['function']['parameters'], format_checker=FormatChecker()).validate(arguments)
    operation = request.operation_id + ':respond'
    ref = 'main-response:' + operation
    previous = store.begin_operation(operation, request.run_id, 'tool')
    if previous is not None:
        return ModelDecisionResult(**{**previous['decision'], 'tool_calls': ()})
    with UnitOfWork(store.database_url) as uow:
        store._assert_fence(uow)
        identity = uow.execute('SELECT * FROM runs WHERE run_id=%s AND account_id=%s',
                              (UUID(request.run_id), UUID(request.account_id))).fetchone()
        root = uow.execute("SELECT execution_id FROM run_executions WHERE account_id=%s AND run_id=%s AND role='root'",
                           (identity['account_id'], identity['run_id'])).fetchone()
        if (identity['source_kind'] != 'chat' or identity['executor_key'] != 'chat_agent'
                or not root or str(root['execution_id']) != request.execution_id):
            raise ValueError('respond_directly requires a Main chat Run')
        # Only receipt IDs actually returned by this Main transcript can be claimed.
        events = uow.execute(
            "SELECT e.payload->'raw_result' AS raw FROM agent_transcript_events e "
            'JOIN agent_transcripts t USING(transcript_id) WHERE t.account_id=%s AND t.run_id=%s '
            "AND e.event_type='tool_result' AND e.payload->'message'->>'name' IN "
            "('accept_work','revise_work','control_work','advance_work','link_work')",
            (identity['account_id'], identity['run_id']),
        ).fetchall()
        allowed = set()
        for event in events:
            raw = event['raw'] or {}
            if raw.get('success') is not True:
                continue
            output = raw.get('output')
            try:
                output = json.loads(output) if isinstance(output, str) else output
                receipt = output.get('command_receipt', {}) if isinstance(output, dict) else {}
                if receipt.get('command_id'):
                    allowed.add(receipt['command_id'])
            except (ValueError, TypeError):
                continue
        if not set(arguments.get('command_refs', [])) <= allowed:
            raise ValueError('operation claims require a command receipt returned in this Run')
        refs = sorted(allowed)
        business = []
        for cid in refs:
            row = uow.execute(
                "SELECT operation,response_body FROM idempotency_commands WHERE account_id=%s "
                "AND idempotency_command_id=%s AND status='completed' AND response_status BETWEEN 200 AND 299",
                (identity['account_id'], UUID(cid)),
            ).fetchone()
            if not row:
                raise ValueError('business command is not committed')
            body = row['response_body']
            work = body.get('work', {})
            business.append({'command_id': cid, 'operation': row['operation'],
                             'work_id': work.get('work_id'), 'status': work.get('status')})
    # Always separate server-verified action facts from model prose.
    answer = arguments['answer'].strip()
    if not answer:
        raise ValueError('answer must be nonempty')
    if not business:
        import re
        if re.search(r'(已(?:经)?(?:成功)?(?:创建|建立|提交|委托|暂停|停止|修改|保存)|(?:任务|委托).{0,8}(?:已创建|已提交|已建立)|(?:created|submitted|saved|paused)\s+(?:the\s+)?(?:task|work|file))', answer, re.I):
            answer = '没有已核验的业务操作成功回执，不能确认委托或修改已建立。\n\n请重试或查询任务的实际状态。'
    elif business:
        facts = '\n'.join(f"已核验命令 {b['operation']}，任务 {b['work_id']}，提交时状态 {b['status']}。" for b in business)
        answer = facts + '\n\n' + answer
    result = {'schema_version': request.schema_version, 'operation_id': operation,
        'decision_type': 'final', 'decision_ref': ref, 'tool_calls': (),
        'transcript_version': decision['transcript_version'] + 1, 'stop_reason': 'respond_directly',
        'display_summary': answer[:240]}
    payload = {'content': answer, 'response_receipt': {'kind': 'response_terminated',
        'business_commands': business}, 'decision': result}
    version = store.complete_operation_with_event(transcript_id=request.transcript_id,
        expected_version=decision['transcript_version'], event_type='tool_result', operation_id=operation,
        event_payload={'message': {'role': 'tool', 'tool_call_id': call_id, 'name': RESPOND,
            'content': json.dumps(payload['response_receipt'], ensure_ascii=False)}},
        result_ref=ref, result_payload=payload)
    result['transcript_version'] = version
    return ModelDecisionResult(**result)
