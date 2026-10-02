"""Atomic Work coordination admission, independent of chat admission."""

from __future__ import annotations

import json

from uuid6 import uuid7

from persistence.repositories import OutboxRepository, RunBudgetRepository
from work_domain.models import continuation
from workspace.resources import ResourceDenied, ResourcePolicy


def admit_work_run(uow, work, requirement, wakeup_id, database, budget_mode):
    account_id, work_id = work['account_id'], work['work_id']
    run_id = uuid7()
    epoch = work['control_epoch']+1
    research = requirement['capability_key']=='research_report'
    executor = 'research_report' if research else 'reminder'
    strategy = 'fixed_workflow' if research else 'deterministic'
    workflow_id = f'hpagent-research-{run_id}' if research else None
    retry = uow.execute('SELECT run_id FROM runs WHERE account_id=%s AND work_id=%s '
                        'AND requirement_revision=%s AND status IN (\'failed\',\'cancelled\') '
                        'ORDER BY created_at DESC,run_id DESC LIMIT 1',
                        (account_id,work_id,requirement['revision'])).fetchone()
    uow.execute('INSERT INTO runs(run_id,account_id,source_kind,work_id,requirement_revision,'
                'work_control_epoch,wakeup_id,strategy_kind,executor_key,workflow_id,agent_strategy,retry_of_run_id) '
                'VALUES (%s,%s,\'work\',%s,%s,%s,%s,%s,%s,%s,NULL,%s)',
                (run_id, account_id, work_id, requirement['revision'], epoch, wakeup_id,
                 strategy, executor, workflow_id, retry['run_id'] if retry else None))
    uow.execute('UPDATE works SET active_coordinator_run_id=%s,control_epoch=%s,'
                'row_version=row_version+1,updated_at=now(),continuation=%s::jsonb WHERE work_id=%s',
                (run_id, epoch, json.dumps(continuation('ready', 'coordinator_admitted')), work_id))
    uow.execute('UPDATE work_wakeups SET state=\'consumed\',run_id=%s WHERE wakeup_id=%s '
                'AND state=\'pending\'', (run_id, wakeup_id))
    uow.execute('UPDATE work_wakeups SET state=\'superseded\' WHERE work_id=%s AND '
                'state=\'pending\' AND expected_revision<=%s', (work_id, requirement['revision']))
    limits = {'sources_discovered':30, 'source_fetches':20, 'research_iterations':3,
              'model_input_tokens':80000, 'model_output_tokens':20000,
              'model_total_tokens':100000, 'model_calls':20, 'wall_time_ms':1800000} if research else {}
    RunBudgetRepository().create_snapshot(uow, run_id, account_id, None, 'work-foundation-v1',
                                          budget_mode, json.dumps(limits), 0)
    policy = ResourcePolicy(database)
    policy.snapshot_in_uow(uow, account_id, run_id, work_id, 'work')
    output = requirement['deliverable_policy']
    if research and output.get('directory_id'):
        from uuid import UUID

        directory = UUID(output['directory_id'])
        operation = output.get('operation', 'create_child')
        entry = UUID(output['entry_id']) if output.get('entry_id') else None
        if operation not in {'create_child','update_content'} or (operation=='update_content') != bool(entry):
            raise ValueError('invalid output operation')
        if not policy._grants(uow, account_id, 'work', work_id, entry or directory, operation):
            raise ResourceDenied('Work output permission missing')
        expected_revision = expected_hash = None
        if entry:
            base = uow.execute('SELECT d.current_revision,d.current_sha256,d.current_file_id '
                               'FROM workspace_nodes n JOIN persistent_file_destinations d '
                               'ON d.account_id=n.account_id AND d.destination_id=n.destination_id '
                               'WHERE n.account_id=%s AND n.node_id=%s AND n.parent_id=%s '
                               'AND n.deleted_at IS NULL FOR SHARE OF d',
                               (account_id, entry, directory)).fetchone()
            if not base or not policy._grants(uow, account_id, 'work', work_id, entry, 'read_content'):
                raise ResourceDenied('Work update requires readable current version')
            expected_revision, expected_hash = base['current_revision'], base['current_sha256']
            candidate = uow.execute('UPDATE run_resource_candidates SET fixed_file_id=%s,fixed_revision=%s,'
                                    'fixed_at=now() WHERE run_id=%s AND node_id=%s RETURNING logical_name',
                                    (base['current_file_id'], expected_revision, run_id, entry)).fetchone()
            if candidate is None:
                raise ResourceDenied('Work update target is outside fixed scope')
            grant = policy._grants(uow, account_id, 'work', work_id, entry, 'read_content')[0]
            uow.execute('INSERT INTO run_resource_access(access_id,account_id,run_id,file_id,node_id,'
                        'basis,grant_id) VALUES (%s,%s,%s,%s,%s,\'workspace_grant\',%s)',
                        (uuid7(), account_id, run_id, base['current_file_id'], entry, grant['grant_id']))
            uow.execute('INSERT INTO run_files(account_id,conversation_id,run_id,file_id,direction,'
                        'logical_name) VALUES (%s,NULL,%s,%s,\'input\',%s)',
                        (account_id, run_id, base['current_file_id'], candidate['logical_name']))
        uow.execute('INSERT INTO research_run_save_intents(run_id,account_id,work_id,requirement_revision,'
                    'target_directory_id,operation,output_kind,policy_version,required,operation_id,'
                    'target_entry_id,expected_revision,expected_sha256) '
                    'VALUES (%s,%s,%s,%s,%s,%s,\'research_markdown\',%s,%s,%s,%s,%s,%s)',
                    (run_id, account_id, work_id, requirement['revision'], directory, operation,
                     requirement['revision'], bool(output.get('required')),
                     f'workspace-save:{run_id}:research_markdown', entry, expected_revision, expected_hash))
    # Reminder execution/schedule dispatch is introduced in Phase 3.
    if research:
        OutboxRepository().enqueue(uow, uuid7(), account_id, 'start_research_run',
                                   f'start-research-run:{run_id}', None, run_id,
                                   json.dumps({'run_id':str(run_id), 'version':1}))
    return dict(uow.execute('SELECT * FROM runs WHERE run_id=%s', (run_id,)).fetchone())
