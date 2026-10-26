"""Main's terminal action. Its receipt proves a response, never a business command."""
import json
import re
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

UNVERIFIED_BUSINESS_RESPONSE = (
    '我目前无法核实这项业务操作是否已成功。请先检查对应任务或文件的实际状态，'
    '再决定是否重新发起，以免重复操作。'
)

# Match bounded object/action/status combinations, not isolated past-tense words.
# File publication/saving and delivery are separate facts from accepting a Work.
_WORK = r'(?:后台任务|研究任务|任务|委托|工作项)'
_FILE = r'(?:文件|报告|文档)'
_CLAIM_ACTIONS = {
    'accept_work': (_WORK, r'创建|建立|提交|委托', r'created|submitted', r'task|work'),
    'revise_work': (_WORK, r'修改|更新|修订', r'updated|modified', r'task|work'),
    'pause_work': (_WORK, r'暂停', r'paused', r'task|work'),
    'resume_work': (_WORK, r'恢复', r'resumed', r'task|work'),
    'stop_work': (_WORK, r'停止', r'stopped', r'task|work'),
    'advance_work': (_WORK, r'推进|重试', r'advanced|retried', r'task|work'),
    'link_work': (_WORK, r'关联', r'linked', r'task|work'),
    'complete_work': (_WORK, r'完成', r'completed', r'task|work'),
    'create_file': (_FILE, r'创建|生成', r'created|generated', r'file|report|document'),
    'save_file': (_FILE, r'保存', r'saved', r'file|report|document'),
    'upload_file': (_FILE, r'上传', r'uploaded', r'file|report|document'),
    'update_file': (_FILE, r'修改|更新', r'updated|modified', r'file|report|document'),
    'delete_file': (_FILE, r'删除', r'deleted', r'file|report|document'),
    'publish_file': (_FILE, r'发布', r'published', r'file|report|document'),
    'send_message': (r'(?:消息|通知|提醒)', r'发送', r'sent', r'message|notification|reminder'),
}
_SUCCESS = r'(?:已(?:经)?(?:成功)?|成功(?:地)?)'
_BENEFICIARY = r'(?:(?:为|帮)(?:你|您))?'
_DETERMINER = r'(?:(?:一|这|那)(?:个|项|份)|该)?'
_CLAIM_PATTERNS = {
    kind: re.compile(
        rf'(?:{_SUCCESS}{_BENEFICIARY}(?:{actions})了?{_DETERMINER}{obj}'
        rf'|{_SUCCESS}{_BENEFICIARY}(?:把|将)?{_DETERMINER}{obj}(?:被)?(?:{actions})了?'
        rf'|{obj}(?:{_SUCCESS}(?:被)?(?:{actions})|(?:{actions})(?:操作)?成功)'
        rf'|\b(?:{english_actions})\s+(?:the\s+|a\s+)?(?:{english_objects})\b'
        rf'|\b(?:{english_objects})\s+(?:(?:has|have)\s+been\s+|was\s+|is\s+)(?:{english_actions})\b)',
        re.I,
    )
    for kind, (obj, actions, english_actions, english_objects) in _CLAIM_ACTIONS.items()
}
_QUOTED = re.compile(r'“[^”]*”|‘[^’]*’|「[^」]*」|『[^』]*』|"[^"\n]*"|\x27[^\x27\n]*\x27|`[^`\n]*`')
_REPORTING = re.compile(r'(?:用户|你|他|她|对方)(?:曾经|之前)?(?:提到|说|表示|声称)|(?:原文|示例|引用)(?:是|为|：|:)')
_NON_ASSERTION = re.compile(r'^\s*(?:如果|假如|若|一旦|当|是否|请问|if\b|when\b)', re.I)
_NEGATED = re.compile(
    r'(?:没有|并未|尚未|未曾|未能|不能|无法|不确定|并非|不是|不代表)'
    r'(?:已|经|能|够|确认|核实|证明|说明|说|真的|地|\s)*$|\b(?:not|never)\s*$', re.I,
)


def explicit_business_success_claims(answer: str) -> set[str]:
    """Recognize a finite set of assertions; a category is not proof of success."""
    # Mask attributed quotations only. Quoted object names in an assertion still
    # participate, and a reported quote cannot hide an independent later claim.
    def quote(match):
        prefix = re.split(r'[。！？!?；;\n，,]', answer[:match.start()])[-1]
        suffix = answer[match.end():]
        content = match.group()[1:-1]
        attributed = _REPORTING.search(prefix) or re.match(r'\s*(?:这句话|这几个字|字样)', suffix)
        if attributed and any(pattern.search(content) for pattern in _CLAIM_PATTERNS.values()):
            return '\0'
        return content

    text = _QUOTED.sub(quote, answer)
    claims = set()
    for clause in re.split(r'[。！？!?；;\n，,\0]|但是|但|不过|然而|同时|并且|而且', text):
        if (_NON_ASSERTION.search(clause) or _REPORTING.search(clause)
                or re.search(r'(?:吗|么)\s*$', clause)):
            continue
        for kind, pattern in _CLAIM_PATTERNS.items():
            if any(not _NEGATED.search(clause[:m.start()]) for m in pattern.finditer(clause)):
                claims.add(kind)
    return claims


def _supported_business_claims(operation, body):
    # Main currently observes Work command receipts only. Neither their requested
    # save policy nor accepting the mandate proves a file save or Work completion.
    work = body.get('work', {})
    if not work.get('work_id'):
        return set()
    if operation in {'accept_work', 'revise_work', 'link_work'}:
        return {operation}
    if operation == 'advance_work' and (body.get('run') or {}).get('run_id'):
        return {operation}
    if (operation in {'pause_work', 'resume_work', 'stop_work'}
            and work.get('status') == {'pause_work': 'paused', 'resume_work': 'active',
                                      'stop_work': 'stopped'}[operation]):
        return {operation}
    return set()


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
        supported_claims = set()
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
            supported_claims.update(_supported_business_claims(row['operation'], body))
            business.append({'command_id': cid, 'operation': row['operation'],
                             'work_id': work.get('work_id'), 'status': work.get('status')})
    # Always separate server-verified action facts from model prose.
    answer = arguments['answer'].strip()
    if not answer:
        raise ValueError('answer must be nonempty')
    if explicit_business_success_claims(answer) - supported_claims:
        answer = UNVERIFIED_BUSINESS_RESPONSE
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
