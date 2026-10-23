"""Account-filtered supplemental facts. No model input bodies or admin access."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from uuid import UUID

# Fixed projection: model request/response content must use authorized HTTP queries.
PROJECTIONS = {
    'workspace_nodes': 'account_id,node_id,parent_id,file_id,kind,deleted_at',
    'works': '*', 'work_requirements': '*', 'runs': 'account_id,run_id,work_id,source_kind,status,requirement_revision,work_control_epoch,result_json,failure_code,failure_message',
    'capability_invocations': '*', 'capability_results': '*', 'capability_result_refs': '*',
    'capability_result_checks': '*', 'capability_result_adoptions': '*', 'capability_observations': '*',
    'capability_acceptance_evidence': '*', 'workspace_save_operations': '*',
    'execution_operations': 'account_id,run_id,execution_id,operation_id,operation_type,status,result_ref',
    'execution_operation_attempts': '*', 'execution_result_receipts': '*', 'execution_control_events': '*',
    'account_model_usage_ledger': '*', 'run_budgets': '*', 'work_budgets': '*',
    'model_dispatches': '*', 'artifact_versions': 'account_id,artifact_id,artifact_version_id,parent_version_id,status,file_id,producing_run_id,producing_execution_id,producing_operation_id',
    'output_publish_operations': '*', 'stored_files': 'account_id,file_id,sha256,size_bytes,status',
}


def provision():
    """Prepare-only: grant a new observer login the enumerated projections."""
    import secrets

    import psycopg
    from psycopg import sql
    password = secrets.token_hex(24)
    with psycopg.connect(os.environ['MIGRATION_DATABASE_URL'], autocommit=True) as db:
        db.execute(sql.SQL('CREATE ROLE s7_observer LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT').format(sql.Literal(password)))
        db.execute('GRANT CONNECT ON DATABASE hpagent TO s7_observer')
        db.execute('GRANT USAGE ON SCHEMA hpagent TO s7_observer')
        for table, columns in PROJECTIONS.items():
            privilege = sql.SQL('SELECT') if columns == '*' else sql.SQL('SELECT ({})').format(sql.SQL(columns))
            db.execute(sql.SQL('GRANT {} ON hpagent.{} TO s7_observer').format(privilege, sql.Identifier(table)))
        db.execute('ALTER ROLE s7_observer SET default_transaction_read_only=on')
    from urllib.parse import urlsplit, urlunsplit
    dsn = urlsplit(os.environ['MIGRATION_DATABASE_URL'])
    observer = urlunsplit((dsn.scheme, f's7_observer:{password}@{dsn.hostname}:{dsn.port}', dsn.path, '', ''))
    Path('/batch/observer.dsn').write_text(observer)
    # Ordinary entitlement provisioning, without inserting any Work/Run/transcript.
    from account.invite_service import EntitlementProfile, RegistrationInviteService
    governance = json.loads(Path('/batch/manifest.json').read_text())['effective_governance']
    invites = {}
    for key in ('ordinary', 'budget_rejection'):
        invites[key] = RegistrationInviteService(os.environ['MIGRATION_DATABASE_URL']).create(
            EntitlementProfile('standard', governance['account_day_tokens'] if key == 'ordinary' else governance['budget_rejection_account_day_tokens'], 'full_safe'),
            max_redemptions=13 if key == 'ordinary' else 1).code
    Path('/batch/invites.json').write_text(json.dumps(invites))


def collect(account):
    import psycopg
    from psycopg import sql
    from psycopg.rows import dict_row
    account = UUID(str(account))
    facts = {'account_id': str(account), 'collected_at': time.time()}
    with psycopg.connect(Path('/batch/observer.dsn').read_text(), row_factory=dict_row) as db:
        db.execute('SET TRANSACTION READ ONLY')
        db.execute('SET LOCAL search_path TO hpagent,public')
        for table, columns in PROJECTIONS.items():
            facts[table] = db.execute(sql.SQL('SELECT {} FROM {} WHERE account_id=%s').format(
                sql.SQL(columns), sql.Identifier(table)), (account,)).fetchall()
    return json.loads(json.dumps(facts, default=str))


def budget_rejection_before_dispatch(terminal, facts, sends):
    return (terminal.get('status') == 'failed'
            and (terminal.get('failure') or {}).get('code') == 'account_daily_model_budget_exhausted'
            and any(r.get('run_id') == terminal.get('run_id') and r.get('failure_code') == 'account_daily_model_budget_exhausted' for r in facts.get('runs', []))
            and not facts.get('model_dispatches') and not sends and not facts.get('works'))


def saved_html(facts, work, directory=None):
    """Only final submitted evidence for the same checked HTML bytes counts."""
    runs = [r for r in facts.get('runs', []) if str(r.get('work_id')) == work and r['status'] == 'succeeded']
    accepted = {str(item['ref']) for r in runs for item in (r.get('result_json') or {}).get('evidence', []) if item['type'] == 'workspace_saved'}
    ids = {str(r['run_id']) for r in runs}
    html = {i['invocation_id'] for i in facts.get('capability_invocations', []) if i['capability_key'] == 'html' and str(i['run_id']) in ids}
    succeeded = {r['invocation_id'] for r in facts.get('capability_results', []) if r['payload']['outcome'] == 'succeeded'}
    refs = {str(r['ref_id']): r for r in facts.get('capability_result_refs', []) if r['type'] == 'artifact_version' and r['invocation_id'] in html & succeeded}
    versions = {str(v['artifact_version_id']): v for v in facts.get('artifact_versions', []) if v['status'] == 'completed'}
    nodes = {str(n['node_id']): n for n in facts.get('workspace_nodes', []) if n['kind'] == 'file' and n['deleted_at'] is None}
    files = {str(f['file_id']): f for f in facts.get('stored_files', []) if f['status'] == 'ready'}
    matches = []
    for e in facts.get('capability_acceptance_evidence', []):
        pred = e['predicate']
        ref = refs.get(str(pred.get('subject_ref')))
        if str(e['evidence_id']) not in accepted or pred['kind'] != 'workspace_saved' or not ref:
            continue
        node = nodes.get(str(pred['target_id']))
        version = versions.get(str(ref['artifact_version_id']))
        file = files.get(str(version['file_id'])) if version else None
        checked = any(c['subject_ref'] == ref['ref_id'] and c['verifier_key'] == 'html.basic' and c['verifier_version'] == '1' and c['verdict'] == 'passed' for c in facts.get('capability_result_checks', []))
        save = next((s for s in facts.get('workspace_save_operations', []) if str(s['source_run_id']) in ids and s['source_kind'] == 'work_auto' and s['node_id'] == pred['target_id'] and s['operation_id'] == pred['operation_id']), None)
        if node and version and file and checked and save and str(node['file_id']) == str(version['file_id']) and file['sha256'] == ref['sha256'] and (directory is None or str(node['parent_id']) == directory):
            matches.append(dict(ref=ref, version=version, save=save, node=node))
    return matches


def judge(facts, *, work, source_message, scenario, provider_events, browser=None, main_run_id=None, directory=None):
    """No empty evidence can pass. Detailed scenario checks supplement this gate."""
    assertions = []
    def check(key, passed, why):
        assertions.append(dict(id=key, passed=bool(passed), reason=why))
    works = [w for w in facts.get('works', []) if str(w['work_id']) == work]
    reqs = [r for r in facts.get('work_requirements', []) if str(r['work_id']) == work]
    check('main_generic_work', len(works) == 1 and any(r['capability_key'] == 'generic_work' and str(r.get('source_message_id')) == source_message for r in reqs),
          'Main must accept a generic Work attributed to the exact user message')
    runs = [r for r in facts.get('runs', []) if str(r.get('work_id')) == work]
    ids = {str(r['run_id']) for r in runs}
    invocations = [i for i in facts.get('capability_invocations', []) if str(i['run_id']) in ids]
    research = [i for i in invocations if i['capability_key'] == 'research']
    html = [i for i in invocations if i['capability_key'] == 'html']
    completed = {e['snapshot_id']: e for e in provider_events if e.get('kind') == 'provider_adapter_completed'}
    dispatches = {str(d['snapshot_id']) for d in facts.get('model_dispatches', [])}
    accounted = {str(u['snapshot_id']) for u in facts.get('account_model_usage_ledger', []) if u['state'] == 'settled' and u['usage_source'] == 'provider'}
    sends = [e for e in completed.values() if str(e['run_id']) in ids]
    check('real_parent_decision', any(e.get('tool_names') for e in sends) and all(e['snapshot_id'] in dispatches & accounted for e in sends),
          'real adapter completions must match dispatched snapshots and provider-settled budget entries')
    check('main_real_accept_decision', any(e.get('kind') == 'provider_adapter_completed' and str(e.get('run_id')) == main_run_id and 'accept_work' in e.get('tool_names', []) for e in provider_events), 'Main accept decision must match its real Provider completion')
    check('research_then_html', bool(html) and (not research if scenario == 'M2' else bool(research)), 'real parent must choose both capabilities')
    refs = facts.get('capability_result_refs', [])
    check('html_uses_markdown_ref', scenario == 'M2' or bool(html) and any(str(ref['ref_id']) in json.dumps(h['descriptor']) for ref in refs if ref['type'] == 'markdown' for h in html),
          'HTML descriptor must fix the exact Research Markdown reference')
    results = {r['invocation_id']: r for r in facts.get('capability_results', [])}
    observations = {r['invocation_id']: r for r in facts.get('capability_observations', [])}
    check('paired_observation', bool(invocations) and all(i['invocation_id'] in observations and
          i['invocation_id'] in results and observations[i['invocation_id']]['producing_operation_id'] == results[i['invocation_id']]['producing_operation_id'] for i in invocations if scenario not in {'M5', 'M6'} or i['invocation_id'] in observations),
          'caller transcript observation must pair with exact committed producer')
    checks = [c for c in facts.get('capability_result_checks', []) if c['invocation_id'] in {i['invocation_id'] for i in invocations}]
    ref_map = {str(r['ref_id']): r for r in refs}
    required_checks = [('html.basic', 'artifact_version')]
    if scenario != 'M2':
        required_checks.append(('research.basic', 'markdown'))
    check('typed_checks', all(any(c['verifier_key'] == verifier and c['verifier_version'] == '1' and c['verdict'] == 'passed'
        and ref_map.get(str(c['subject_ref']), {}).get('type') == kind
        and ref_map.get(str(c['subject_ref']), {}).get('invocation_id') == c['invocation_id']
        and ref_map.get(str(c['evidence_ref']), {}).get('type') == 'check_evidence'
        and ref_map.get(str(c['evidence_ref']), {}).get('invocation_id') == c['invocation_id']
        for c in checks) for verifier, kind in required_checks), 'exact registered subject/version checks required')
    if scenario in {'M1', 'M2', 'M3', 'M4'}:
        check('work_completed', bool(works) and works[0]['status'] == 'completed', 'Work completion is a separate database fact')
        check('run_succeeded', any(r['status'] == 'succeeded' for r in runs), 'a coordinator Run must succeed')
        check('saved', bool(saved_html(facts, work, directory)), 'final typed evidence must identify the same checked HTML bytes, save operation and authorized destination')
        check('acceptance_evidence', any(str(e['run_id']) in ids for e in facts.get('capability_acceptance_evidence', [])), 'required typed evidence must be persisted')
        check('browser_interaction', browser and browser.get('passed') is True, 'independent real sandbox iframe interaction is required')
    if scenario == 'M3':
        outcomes = [results[h['invocation_id']]['payload']['outcome'] for h in html if h['invocation_id'] in results]
        check('insufficient_then_repair', len(research) == 1 and len(outcomes) >= 2 and outcomes[0] == 'insufficient' and 'succeeded' in outcomes[1:], 'one research result reused after actual HTML insufficiency')
    return assertions


if __name__ == '__main__':
    provision()
