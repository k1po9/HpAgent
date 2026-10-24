"""Deterministic evidence for an unambiguous, parent-observed text deliverable."""

from uuid import UUID

from run_domain.acceptance import ResultAdoptionService
from run_domain.capability_contracts import AcceptancePredicate
from run_domain.lifecycle import RunLifecycleService


def register_text_output(uow, facts, invocation_id, result):
    run, parent = facts._context(uow)
    if (run['source_kind'] != 'work' or run['executor_key'] != 'work_agent'
            or parent['role'] != 'root'):
        return []
    RunLifecycleService.check_work(uow, run)
    invocation = uow.execute(
        'SELECT capability_key,contract_version,caller_execution_id FROM capability_invocations '
        'WHERE account_id=%s AND run_id=%s AND invocation_id=%s',
        (run['account_id'], run['run_id'], invocation_id),
    ).fetchone()
    if (not invocation or invocation['capability_key'] != 'text'
            or invocation['contract_version'] != 1
            or invocation['caller_execution_id'] != parent['execution_id']):
        return []
    requirement = run['input_snapshot']['requirement']
    outputs = [r for r in result['outputs'] if r['type'] != 'check_evidence']
    if requirement.get('deliverables'):
        service = ResultAdoptionService(uow, facts)
        evidence = []
        for output in outputs:
            checks = [c for c in result['checks'] if c['subject_ref'] == output['ref_id']]
            targets = [t for t in requirement['deliverables']['items'] if t['required'] and any(
                c['verifier_key'] == f"file.format.{t['format']}" and c['verifier_version'] == '1' for c in checks)]
            if len(targets) != 1:
                continue
            target = targets[0]
            ref = facts.read_ref(uow, UUID(output['ref_id']))
            evidence.append(service.evidence(target['output_criterion_id'], AcceptancePredicate('output_exists', ref.ref_id)))
            if any(c['verdict'] == 'passed' and c['verifier_key'] == f"file.format.{target['format']}"
                   and c['verifier_version'] == '1' for c in checks):
                evidence.append(service.evidence(target['format_criterion_id'], AcceptancePredicate(
                    'check_passed', ref.ref_id, verifier_key=f"file.format.{target['format']}", verifier_version='1')))
        return evidence
    if result['outcome'] != 'succeeded':
        return []
    criteria = [c for c in requirement['acceptance_criteria']
                if c['required'] and c['evidence_types'] == ['output_exists']]
    # Several output conditions can name different deliverables. Do not guess a mapping.
    if len(criteria) != 1 or len(outputs) != 1:
        return []
    ref = facts.read_ref(uow, UUID(outputs[0]['ref_id']))
    if ref.type != 'markdown' or ref.producing_invocation_id != invocation_id:
        return []
    # UnitOfWork accepts an existing UoW: evidence and observation commit/rollback together.
    evidence = ResultAdoptionService(uow, facts).evidence(
        criteria[0]['id'], AcceptancePredicate('output_exists', ref.ref_id),
    )
    return [evidence]


def observed_text_evidence(uow, run):
    """Only trusted observation metadata, never assistant text or arbitrary ready files.

    The latest successful publication wins; prior publications and evidence remain immutable.
    Current fact validation is still performed by generic_result and completion policy.
    """
    if run.get('source_kind') != 'work' or run.get('executor_key') != 'work_agent':
        return []
    rows = uow.execute(
        "SELECT e.payload->'output_exists_evidence' AS evidence "
        'FROM capability_observations o JOIN capability_invocations i USING(account_id,invocation_id) '
        'JOIN capability_results cr USING(account_id,invocation_id) '
        'JOIN agent_transcript_events e ON e.transcript_id=o.transcript_id AND e.sequence=o.sequence '
        'JOIN agent_transcripts t ON t.transcript_id=o.transcript_id '
        'JOIN run_executions x ON x.execution_id=i.caller_execution_id '
        "WHERE o.account_id=%s AND o.run_id=%s AND i.run_id=o.run_id AND x.role='root' "
        'AND o.caller_execution_id=x.execution_id AND t.execution_id=x.execution_id '
        'AND t.account_id=o.account_id AND t.run_id=o.run_id '
        'AND i.requirement_revision=%s AND i.work_control_epoch=%s '
        "AND i.capability_key='text' AND i.contract_version=1 "
        "AND e.event_type='tool_result' AND e.payload ? 'output_exists_evidence' "
        'ORDER BY e.sequence DESC LIMIT 100',
        (run['account_id'], run['run_id'], run['requirement_revision'], run['work_control_epoch']),
    ).fetchall()
    selected: dict[str, dict] = {}
    for row in rows:
        for item in row['evidence']:
            selected.setdefault(item['criterion_id'], item)
    return list(selected.values())
