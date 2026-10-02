"""Explicit current-revision acceptance of persisted execution evidence."""

from __future__ import annotations

import json

from work_domain.persistence import WorkRepository


class WorkCompletionPolicy:
    @staticmethod
    def accept(uow, work: dict, run: dict) -> bool:
        if work['status'] != 'active' or work['active_coordinator_run_id'] is not None or work['continuation'].get('operation_ref') or (
            run['status'] != 'succeeded' or run['account_id'] != work['account_id'] or
            run['work_id'] != work['work_id'] or
            run['requirement_revision'] != work['current_requirement_revision'] or
            run['work_control_epoch'] != work['control_epoch']
        ):
            raise ValueError('result does not match current mandate')
        requirement = WorkRepository.requirement(uow, work['account_id'], work['work_id'],
                                                 work['current_requirement_revision'])
        result = run['result_json'] or {}
        evidence = result.get('evidence', [])
        criteria = requirement['acceptance_criteria']
        if not criteria or not evidence:
            raise ValueError('explicit acceptance criteria and evidence are required')
        for criterion in criteria:
            if criterion['required'] and not any(
                item['criterion_id'] == criterion['id'] and item['type'] in criterion['evidence_types']
                and item.get('ref') for item in evidence
            ):
                raise ValueError('required acceptance evidence missing')
        if requirement['deliverable_policy'].get('required'):
            row = uow.execute(
                'SELECT i.state, CASE WHEN i.operation=\'create_child\' THEN EXISTS('
                'SELECT 1 FROM workspace_save_operations o WHERE o.account_id=i.account_id '
                'AND o.operation_id=i.operation_id AND o.node_id=i.entry_id '
                'AND o.source_run_id=i.run_id) ELSE EXISTS(SELECT 1 FROM workspace_version_operations o '
                'WHERE o.account_id=i.account_id AND o.operation_id=i.operation_id '
                'AND o.node_id=i.entry_id AND o.source_run_id=i.run_id) END AS committed '
                'FROM research_run_save_intents i WHERE i.account_id=%s AND i.run_id=%s '
                'AND i.work_id=%s AND i.requirement_revision=%s AND i.required',
                (work['account_id'], run['run_id'], work['work_id'], run['requirement_revision'])).fetchone()
            if not row or row['state'] != 'succeeded' or not row['committed']:
                raise ValueError('required Workspace save has not committed')
        if requirement['completion_mode'] == 'ongoing':
            WorkRepository.event(uow, work, 'result_accepted', run_id=run['run_id'], evidence=evidence)
            return False
        receipt = {'schema_version': 1, 'run_id': str(run['run_id']),
                   'requirement_revision': run['requirement_revision'], 'evidence': evidence,
                   'evaluator': 'work_completion_policy_v1'}
        updated = uow.execute(
            'UPDATE works SET status=\'completed\',completed_requirement_revision=%s,'
            'completion_receipt=%s::jsonb,completed_at=now(),row_version=row_version+1,'
            'updated_at=now(),continuation=\'{"schema_version":1,"kind":"none","reason":"criteria_satisfied"}\' '
            'WHERE account_id=%s AND work_id=%s RETURNING *',
            (run['requirement_revision'], json.dumps(receipt), work['account_id'], work['work_id'])).fetchone()
        uow.execute('UPDATE work_wakeups SET state=\'superseded\' WHERE work_id=%s AND state=\'pending\'',
                    (work['work_id'],))
        WorkRepository.event(uow, dict(updated), 'completed', run_id=run['run_id'], evidence=evidence)
        return True
