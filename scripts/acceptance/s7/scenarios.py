"""S7-1 HTTP/UI scenarios. No direct Work/Run/transcript mutations."""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from uuid import uuid4

import httpx
from s7.core import Ledger, Rejected
from s7.evidence import budget_rejection_before_dispatch, collect, judge, saved_html

BATCH = Path('/batch')
M = json.loads((BATCH / 'manifest.json').read_text())
LEDGER = Ledger(Path('/campaign/ledger.sqlite'), M)


class SampleFailed(Exception):
    pass


def write(name, value):
    if name.endswith('.result.json') and isinstance(value, dict) and value.get('reuse'):
        value = {**value, 'reuse': {k: v for k, v in value['reuse'].items() if k != 'cookies'}}
    (BATCH / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str))


def wait_for(callback, seconds, label):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if LEDGER.summary()['uncertain_requests']:
            # Sending is not unknown while the same live Worker is still awaiting it.
            with LEDGER.connect() as db:
                uncertain = db.execute('SELECT 1 FROM sends WHERE state="uncertain"').fetchone()
            if uncertain:
                raise Rejected('unknown Provider outcome; stop without resend')
        found = callback()
        if found:
            return found
        time.sleep(0.5)
    raise SampleFailed(label + ': deadline exceeded')


class Product:
    def __init__(self, current, existing=None):
        self.sample = current
        self.client = httpx.Client(base_url='http://gateway:8080', timeout=15,
            headers={'Origin': os.environ['WEB_PUBLIC_ORIGIN']})
        if existing:
            self.client.cookies.update(existing)
        self.csrf = None
        self.account = None

    def call(self, method, path, data=None, etag=None):
        headers = {'Idempotency-Key': str(uuid4())}
        if self.csrf:
            headers['X-CSRF-Token'] = self.csrf
        if etag:
            headers['If-Match'] = etag
        # Store exact key+payload before send. An uncertain command is never
        # retried under a new identity; operator may replay this recorded command.
        record = dict(sample=self.sample, method=method, path=path, key=headers['Idempotency-Key'],
                      payload=({'credentials_redacted': True} if path.startswith('/auth/') else data), at=time.time(), etag=etag)
        if method != 'GET':
            LEDGER.event('product_command_intent', record)
        response = self.client.request(method, path, json=data, headers=headers)
        if method != 'GET':
            LEDGER.event('product_command_response', {**record, 'http_status': response.status_code})
        if response.status_code >= 400:
            write(f'{self.sample}.http-error.json', {'path': path, 'status': response.status_code, 'body': response.json()})
            raise SampleFailed(f'product command {path} returned {response.status_code}')
        return response.json() if response.content else {}

    def register(self, budget=False):
        invites = json.loads((BATCH / 'invites.json').read_text())
        self.call('POST', '/auth/register', dict(username='accept_' + uuid4().hex[:15],
            password=uuid4().hex, invite_code=invites['budget_rejection' if budget else 'ordinary']))
        me = self.call('GET', '/api/v1/me')
        self.csrf, self.account = me['csrf_token'], me['account']['account_id']

    def me(self):
        me = self.call('GET', '/api/v1/me')
        self.csrf, self.account = me['csrf_token'], me['account']['account_id']

    def work(self, wid):
        return self.call('GET', '/api/v1/works/' + wid)['work']

    def mutate_work(self, wid, action, data=None, method='POST'):
        w = self.work(wid)
        result = self.call(method, f'/api/v1/works/{wid}/{action}', data or {},
            etag=f'"work-{wid}-v{w["row_version"]}"')
        LEDGER.event('control_committed', dict(sample=self.sample, action=action, work_id=wid,
                     before_epoch=w['control_epoch'], after_epoch=result['work']['control_epoch'], at=time.time()))
        return result['work']


def provider_events(current):
    with LEDGER.connect() as db:
        rows = db.execute('SELECT kind,body FROM events WHERE kind="provider_adapter_completed"').fetchall()
        sends = {row[0] for row in LEDGER.batch_sends(current)}
    return [dict(json.loads(body), kind=kind) for kind, body in rows if json.loads(body)['snapshot_id'] in sends]


def collect_http_inputs(product, facts):
    """Use the normal account visibility query, never the observer/admin DSN."""
    captured = {}
    public = {'collected_at': time.time(), 'works': {}, 'runs': {}, 'versions': {}}
    for work in facts['works']:
        wid = work['work_id']
        public['works'][wid] = {name: product.call('GET', f'/api/v1/works/{wid}' + suffix) for name, suffix in [('detail', ''), ('runs', '/runs'), ('events', '/events'), ('artifacts', '/artifacts'), ('resources', '/resources')]}
    for version in facts['artifact_versions']:
        vid = version['artifact_version_id']
        public['versions'][vid] = product.call('GET', '/api/v1/artifact-versions/' + vid)
    for run in facts['runs']:
        rid = str(run['run_id'])
        public['runs'][rid] = dict(detail=product.call('GET', f'/api/v1/runs/{rid}'), capability_results=product.call('GET', f'/api/v1/runs/{rid}/capability-results'))
        listing = product.call('GET', f'/api/v1/runs/{rid}/model-inputs')
        for item in listing['items']:
            identity = item['snapshot_id']
            value = product.call('GET', '/api/v1/model-inputs/' + identity)
            captured[identity] = value
    write(f'{product.sample}.http.json', public)
    write(f'{product.sample}.model-inputs.json', dict(collected_at=time.time(), snapshots=captured))
    return captured


def leave_page(product, conversation, source):
    from playwright.sync_api import expect, sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=M['resources']['browser_executable'], args=['--no-sandbox'])
        context = browser.new_context(extra_http_headers={'Origin': os.environ['WEB_PUBLIC_ORIGIN']})
        context.add_cookies([{'name': c.name, 'value': c.value, 'url': 'http://gateway:8080', 'httpOnly': True, 'sameSite': 'Lax'} for c in product.client.cookies.jar])
        try:
            page = context.new_page()
            page.goto(f'http://gateway:8080/#/ai/{conversation}?message={source}', wait_until='domcontentloaded')
            expect(page.locator(f'[data-message-id="{source}"]')).to_be_visible(timeout=15000)
            page.get_by_role('button', name='空间', exact=True).click()
            page.close()
            return {'passed': True, 'left_at': time.time(), 'browser_version': browser.version}
        finally:
            browser.close()


def browser_check(product, work, artifact, run_id, subject_ref, expected_sha):
    from playwright.sync_api import expect, sync_playwright
    outcome = {'passed': False, 'quality_review': 'not_covered'}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, executable_path=M['resources']['browser_executable'], args=['--no-sandbox'])
        context = browser.new_context(extra_http_headers={'Origin': os.environ['WEB_PUBLIC_ORIGIN']})
        context.add_cookies([{'name': c.name, 'value': c.value, 'domain': 'gateway', 'path': '/',
                             'httpOnly': True, 'sameSite': 'Lax'} for c in product.client.cookies.jar])
        context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        try:
            page.goto(f'http://gateway:8080/#/tasks?work={work}', wait_until='domcontentloaded')
            expect(page.get_by_role('heading', name=product.work(work)['title'], exact=True)).to_be_visible(timeout=15000)
            page.reload(wait_until='domcontentloaded')
            page.get_by_role('button', name='定位来源消息', exact=True).click(timeout=15000)
            expect(page.locator('[data-message-id="' + product.work(work)['sources'][0]['source_message_id'] + '"]')).to_be_visible(timeout=15000)
            page.get_by_role('button', name='返回来源任务', exact=True).click(timeout=15000)
            # Traverse the existing task -> Run -> exact capability result UI.
            page.get_by_role('tab', name='高级详情', exact=True).click()
            form = page.get_by_role('button', name='查询执行与诊断', exact=True).locator('..')
            form.get_by_label('执行编号', exact=True).fill(run_id)
            form.get_by_role('button', name='查询执行与诊断', exact=True).click()
            page.get_by_role('button', name='读取能力成果', exact=True).click(timeout=15000)
            page.locator('section[aria-label="能力成果"] article').filter(has_text=subject_ref).get_by_role('button', name='查看 HTML 成果版本', exact=True).click(timeout=15000)
            frame_element = page.get_by_title('Artifact 预览')
            expect(frame_element).to_have_attribute('sandbox', 'allow-scripts')
            import hashlib
            preview_sha = hashlib.sha256((frame_element.get_attribute('srcdoc') or '').encode()).hexdigest()
            if preview_sha != expected_sha:
                raise SampleFailed('preview bytes do not match selected immutable result')
            frame = frame_element.content_frame
            group = frame.locator('article').filter(has_text=re.compile('TaskGroup')).first
            lock = frame.locator('article').filter(has_text=re.compile('Lock')).first
            expect(group).to_be_visible()
            expect(lock).to_be_visible()
            frame.get_by_role('button', name=re.compile('^编排$')).click()
            expect(group).to_be_visible()
            expect(lock).to_be_hidden()
            frame.get_by_role('button', name=re.compile('^同步$')).click()
            expect(group).to_be_hidden()
            expect(lock).to_be_visible()
            frame.get_by_role('button', name=re.compile('^全部$')).click()
            expect(group).to_be_visible()
            expect(lock).to_be_visible()
            if product.sample.startswith('M2-'):
                search = frame.get_by_label('搜索条目', exact=True)
                search.fill('TaskGroup')
                expect(group).to_be_visible()
                expect(lock).to_be_hidden()
                search.fill('')
                expect(group).to_be_visible()
                expect(lock).to_be_visible()
            outcome.update(passed=True, preview_sha256=preview_sha, browser_version=browser.version, artifact_version_id=artifact)
        except Exception as exc:
            outcome['reason'] = type(exc).__name__ + ': ' + str(exc)[:400]
        finally:
            context.tracing.stop(path=str(BATCH / f'{product.sample}.browser.zip'))
            browser.close()
    return outcome


def barrier(product, wid, current):
    marker = BATCH / f'{current}.barrier.json'
    target = json.loads(marker.read_text())
    facts = collect(product.account)
    result = next((r for r in facts['capability_results'] if r['invocation_id'] == target['invocation_id']), None)
    if not result or result['payload']['outcome'] != 'succeeded' or not any(r['type'] == 'artifact_version' for r in result['payload']['outputs']) or any(o['invocation_id'] == target['invocation_id'] for o in facts['capability_observations']):
        raise SampleFailed('barrier did not hit committed result before parent observation')
    write(f'{current}.before.json', facts)
    kind = current.split('-')[0]
    if kind == 'M4':
        write('restart-worker.json', {'sample': current, 'target': target, 'at': time.time()})
        wait_for(lambda: (BATCH / 'restart-worker-consumed.json').exists(), 15, 'owned Worker restart')
    elif kind == 'M6':
        grants = product.call('GET', f'/api/v1/works/{wid}/resources')['grants']
        for grant in grants:
            if 'create_child' in grant['operations']:
                product.mutate_work(wid, 'resources/' + grant['grant_id'], method='DELETE')
        (BATCH / f'{current}.release').touch()
    elif kind == 'M5':
        paused = product.mutate_work(wid, 'pause')
        at = time.time()
        (BATCH / f'{current}.release').touch()
        wait_for(lambda: product.work(wid)['status'] == 'paused', 120, 'pause acknowledged at HTML barrier')
        snapshot = product.work(wid)
        if snapshot['status'] != 'paused':
            raise SampleFailed('Work advanced while paused')
        (BATCH / f'{current}.resume-requested').touch()
        resumed = product.mutate_work(wid, 'resume')
        new = wait_for(lambda: (w if (w := product.work(wid))['active_coordinator_run_id'] and w['active_coordinator_run_id'] != target['run_id'] else None), 60, 'new coordinator admission')
        wait_for(lambda: (BATCH / f'{current}.resumed-barrier.json').exists(), 60, 'resumed root segment released before next model')
        stopped = product.mutate_work(wid, 'stop')
        (BATCH / f'{current}.resumed-release').touch()
        write(f'{current}.controls.json', dict(paused=paused, resumed=resumed, new=new, stopped=stopped, paused_at=at))
    return target


def run_sample(current, parent=None, existing=None):
    (BATCH / 'active-sample').write_text(current)
    scenario = current.split('-')[0]
    record = {'sample': current, 'outcome': 'fail', 'assertions': [], 'started_at': time.time()}
    product = Product(current, existing.get('cookies') if existing else parent.get('cookies') if parent else None)
    wid, source = None, None
    deadline = time.monotonic() + M['limits']['scenario_seconds']
    try:
        if parent or existing:
            product.me()
        else:
            product.register(scenario == 'M7')
        record['account_id'] = product.account
        workspace = product.call('GET', '/api/v1/workspace')
        directory = product.call('POST', '/api/v1/workspace/directories', {'parent_id': workspace['root_id'], 'name': '验收成果-' + current})['node_id']
        cid = product.call('POST', '/api/v1/conversations', {'title': current})['conversation']['conversation_id']
        text = M['prompt'].format(sources='、'.join(s['url'] for s in M['sources']), directory=directory)
        files = []
        if parent:
            files = [parent['file_id']]
            text = M['revision_prompt'].format(version=parent['version_id'], directory=directory)
        sent = product.call('POST', f'/api/v1/conversations/{cid}/messages', dict(content=text, agent_strategy='react', file_ids=files))
        source = sent['user_message']['message_id']
        rid = sent['run']['run_id']
        record.update(source_message_id=source, main_run_id=rid, conversation_id=cid, directory_id=directory)
        def accepted():
            works = product.call('GET', '/api/v1/works')['items']
            match = [w for w in works if any(s['source_message_id'] == source for s in w['sources'])]
            if match:
                if len(match) != 1:
                    raise SampleFailed('Main created duplicate Work')
                return match[0]
            main = product.call('GET', '/api/v1/runs/' + rid)['run']
            if main['status'] in {'succeeded', 'failed', 'cancelled'}:
                raise SampleFailed('Main ended without an accurately attributed Work')
            return None
        if scenario == 'M7':
            terminal = wait_for(lambda: (r if (r := product.call('GET', '/api/v1/runs/' + rid)['run'])['status'] in {'succeeded', 'failed', 'cancelled'} else None), 150, 'budget rejection')
            facts = collect(product.account)
            passed = budget_rejection_before_dispatch(terminal, facts, LEDGER.batch_sends(current))
            record['assertions'] = [dict(id='account_day_rejection_before_dispatch', passed=passed, reason='dedicated 1-token entitlement must reject before any provider send or Work acceptance')]
            record['outcome'] = 'pass' if passed else 'fail'
            return record
        w = wait_for(accepted, 240, 'Main acceptance')
        wid = w['work_id']
        record['work_id'] = wid
        write(f'{current}.work.json', {'work_id': wid, 'source_message_id': source, 'at': time.time()})
        if w['requirement']['capability_key'] != 'generic_work':
            raise SampleFailed('Main selected a fixed entry instead of generic_work')
        # Product commands manage early automatic admission. Never edit a snapshot.
        if w['status'] == 'active':
            product.mutate_work(wid, 'pause')
            wait_for(lambda: product.work(wid)['status'] == 'paused', 150, 'initial pause acknowledged')
        product.mutate_work(wid, 'resources', dict(node_id=directory, operations=['list_metadata', 'create_child'], recursive=False))
        if files:
            product.mutate_work(wid, 'inputs', dict(file_id=files[0], source_message_id=source, purpose='explicit selected prior HTML version'))
            product.mutate_work(wid, 'artifacts', dict(artifact_version_id=parent['version_id'], role='input'))
        product.mutate_work(wid, 'resume')
        # Leaving the UI does not stop the dedicated durable Worker.
        revision = product.work(wid)['current_requirement_revision']
        if scenario == 'M1':
            record['leave_page'] = leave_page(product, cid, source)
            wait_for(lambda: product.call('GET', '/api/v1/runs/' + rid)['run']['status'] in {'succeeded', 'failed', 'cancelled'}, 150, 'Main chat slot release')
            product.call('POST', f'/api/v1/conversations/{cid}/messages', dict(content=M['ordinary_prompt'], agent_strategy='react'))
        hit = False
        while time.monotonic() < deadline:
            if not hit and scenario in {'M4', 'M5', 'M6'} and (BATCH / f'{current}.barrier.json').exists():
                barrier(product, wid, current)
                hit = True
            w = product.work(wid)
            runs = product.call('GET', f'/api/v1/works/{wid}/runs')['items']
            active = [r for r in runs if r['status'] in {'queued', 'running', 'cancelling'}]
            if w['status'] in {'completed', 'stopped'} and not active:
                break
            if not active and w['continuation'].get('kind') in {'blocked', 'awaiting_input', 'failed'}:
                break
            with LEDGER.connect() as db:
                if db.execute('SELECT 1 FROM sends WHERE state="uncertain"').fetchone():
                    raise Rejected('Provider result uncertain; stop batch')
            time.sleep(0.5)
        else:
            raise SampleFailed('scenario deadline exceeded; no automatic repeat')
        facts = collect(product.account)
        write(f'{current}.facts.json', facts)
        inputs = collect_http_inputs(product, facts)
        invs = {i['invocation_id'] for i in facts['capability_invocations'] if any(str(r['run_id']) == str(i['run_id']) and str(r.get('work_id')) == wid for r in facts['runs'])}
        versions = [v for v in facts['artifact_versions'] if v['status'] == 'completed' and any(i['invocation_id'] in invs and i['child_execution_id'] == v['producing_execution_id'] for i in facts['capability_invocations'])]
        saved = saved_html(facts, wid, directory)
        delivered = {item['version']['artifact_version_id'] for item in saved}
        selected = next((v for v in versions if v['artifact_version_id'] in delivered), None) if len(delivered) == 1 else None
        subject = next((r for r in facts['capability_result_refs'] if selected and r.get('artifact_version_id') == selected['artifact_version_id']), None)
        browser = browser_check(product, wid, selected['artifact_version_id'], selected['producing_run_id'], subject['ref_id'], subject['sha256']) if subject and scenario in {'M1', 'M2', 'M3', 'M4'} else None
        record['browser'] = browser
        record['assertions'] = judge(facts, work=wid, source_message=source, scenario=scenario, provider_events=provider_events(current), browser=browser, main_run_id=rid, directory=directory)
        sent_events = provider_events(current)
        def input_matches(event):
            import hashlib
            value = inputs.get(event['snapshot_id'], {})
            body = value.get('model_input', {}).get('provider_request_body')
            if value.get('visibility') != 'full_safe' or body is None:
                return False
            raw = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
            return hashlib.sha256(raw).hexdigest() == event['request_sha256']
        record['assertions'].append(dict(id='authorized_exact_model_inputs', passed=bool(sent_events) and all(input_matches(e) for e in sent_events if str(e['run_id']) in {r['run_id'] for r in facts['runs']}), reason='authorized HTTP snapshots must match the actual adapter request hashes'))
        if scenario == 'M1':
            record['assertions'].append(dict(id='ordinary_chat_preserves_requirement', passed=w['current_requirement_revision'] == revision, reason='normal chat cannot revise the background Work'))
        if scenario == 'M3':
            with LEDGER.connect() as db:
                corruptions = [json.loads(b) for (b,) in db.execute('SELECT body FROM events WHERE kind="candidate_corrupted"')]
            corruptions = [e for e in corruptions if e.get('sample') == current and e['run_id'] in {r['run_id'] for r in facts['runs']}]
            synthesis = [e for e in sent_events if e['phase'] == 'research_synthesis' and e['run_id'] in {r['run_id'] for r in facts['runs']}]
            record['assertions'].append(dict(id='one_corruption_one_synthesis', passed=len(corruptions) == 1 and corruptions[0]['original_complete_html'] and len(synthesis) == 1, reason='one complete raw candidate corrupted before original hardening; no new research synthesis'))
        if scenario in {'M4', 'M5', 'M6'}:
            record['assertions'].append(dict(id='declared_barrier_hit', passed=hit, reason='committed HTML result must precede observation/control'))
        if scenario == 'M4' and hit:
            before = json.loads((BATCH / f'{current}.before.json').read_text())
            target = json.loads((BATCH / f'{current}.barrier.json').read_text())
            old = [r for r in before['capability_results'] if r['invocation_id'] == target['invocation_id']]
            new = [r for r in facts['capability_results'] if r['invocation_id'] == target['invocation_id']]
            original_sends = [e for e in provider_events(current) if e['operation_id'] == target['operation_id']]
            record['assertions'].append(dict(id='recover_original_committed_producer', passed=old == new and len(original_sends) == 1, reason='same producer result/identity and one actual producing model completion across restart'))
        if scenario == 'M5':
            controls = json.loads((BATCH / f'{current}.controls.json').read_text()) if hit else {}
            before = json.loads((BATCH / f'{current}.before.json').read_text()) if hit else {}
            original = json.loads((BATCH / f'{current}.barrier.json').read_text()) if hit else {}
            old_saves = {s['operation_id'] for s in before.get('workspace_save_operations', []) if s['source_run_id'] == original.get('run_id')}
            after_old_saves = {s['operation_id'] for s in facts['workspace_save_operations'] if s['source_run_id'] == original.get('run_id')}
            old_caller = next((op for op in facts['execution_operations'] if op['operation_id'] == original.get('invocation_id')), {})
            record['assertions'].append(dict(id='old_fence_did_not_observe_or_save', passed=hit and old_caller.get('status') != 'completed' and old_saves == after_old_saves, reason='pause invalidates the old parent before observation/save'))
            if existing:
                import hashlib
                listing = product.call('GET', '/api/v1/workspace/search?source_run_id=' + existing['source_run_id'])
                response = product.client.get('/api/v1/files/' + existing['file_id'] + '/content')
                found = any(n['node_id'] == existing['node_id'] for n in listing['items'])
                preserved = response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == existing['sha256']
                record['assertions'].append(dict(id='prior_saved_deliverable_findable', passed=found and preserved, reason='previous saved M1 entry and immutable bytes survive controls of the new Work'))
            else:
                record['assertions'].append(dict(id='prior_saved_deliverable_findable', passed=False, reason='no valid saved baseline'))
            time.sleep(4)
            later = product.work(wid)
            record['assertions'].append(dict(id='stopped_no_auto_wakeup', passed=later['status'] == 'stopped' and later['active_coordinator_run_id'] is None, reason='recheck across two normal scheduler intervals'))
            record['assertions'].append(dict(id='pause_resume_stop', passed=hit and w['status'] == 'stopped' and controls['new']['control_epoch'] > controls['paused']['control_epoch'], reason='new coordinator/fence after resume; stopped cannot complete'))
        if scenario == 'M6':
            ids = {r['run_id'] for r in facts['runs'] if str(r.get('work_id')) == wid}
            record['assertions'].append(dict(id='revoked_save_rejected', passed=hit and w['status'] != 'completed' and not any(s.get('source_run_id') in ids for s in facts['workspace_save_operations']), reason='no permanent save or completion after revocation'))
        if parent:
            old = product.call('GET', '/api/v1/artifact-versions/' + parent['version_id'])
            parentwork = product.work(parent['work_id'])
            old_refs = {r['ref_id'] for r in facts['capability_result_refs'] if r.get('artifact_version_id') == parent['version_id']}
            html_inputs = [i['descriptor']['inputs'] for i in facts['capability_invocations'] if i['invocation_id'] in invs and i['capability_key'] == 'html']
            adopted = any(a['ref_id'] in old_refs and a['run_id'] in {r['run_id'] for r in facts['runs'] if str(r.get('work_id')) == wid} for a in facts['capability_result_adoptions'])
            record['assertions'].append(dict(id='exact_old_html_adopted', passed=adopted and any(h.get('previous_ref') in old_refs for h in html_inputs), reason='new parent explicitly adopts the selected immutable old result and fixes HTML previous_ref'))
            record['assertions'].append(dict(id='old_work_version_immutable', passed=parentwork['status'] == 'completed' and old.get('version') == parent['version_response'].get('version') and any(v['parent_version_id'] == parent['version_id'] for v in versions), reason='new Work/version must retain exact selected parent; old delivery/bytes unchanged'))
        record['outcome'] = 'pass' if record['assertions'] and all(a['passed'] for a in record['assertions']) else 'fail'
        if selected:
            v = selected
            saved_entry = next(item for item in saved if item['version']['artifact_version_id'] == v['artifact_version_id'])
            record['reuse'] = dict(cookies=dict(product.client.cookies), work_id=wid,
                file_id=v['file_id'], version_id=v['artifact_version_id'], node_id=saved_entry['node']['node_id'], sha256=saved_entry['ref']['sha256'], source_run_id=v['producing_run_id'],
                version_response=product.call('GET', '/api/v1/artifact-versions/' + v['artifact_version_id']))
    except Rejected as exc:
        record.update(outcome='uncertain' if LEDGER.summary()['uncertain_requests'] else 'environment_error', reason=str(exc))
    except SampleFailed as exc:
        record['reason'] = str(exc)
    except Exception as exc:
        record.update(outcome='environment_error', reason=type(exc).__name__ + ': ' + str(exc)[:300])
    finally:
        if product.account:
            try:
                if wid and record['outcome'] != 'pass' and product.work(wid)['status'] in {'active', 'paused'}:
                    product.mutate_work(wid, 'stop')
                # Preserve actual in-flight billing before selecting another sample.
                until = time.monotonic() + M['limits']['request_seconds'] + 10
                while time.monotonic() < until:
                    with LEDGER.connect() as db:
                        live = db.execute('SELECT 1 FROM sends WHERE state="sending"').fetchone()
                    if not live:
                        break
                    time.sleep(0.5)
                write(f'{current}.facts.json', collect(product.account))
            except Exception as exc:
                record['collection_error'] = type(exc).__name__ + ': ' + str(exc)[:200]
                record['outcome'] = 'environment_error'
        record['ended_at'] = time.time()
        record['budget'] = LEDGER.summary()
        write(f'{current}.result.json', record)
        product.client.close()
    return record


def main():
    phase = sys.argv[1]
    if phase == 'verify':
        reports = [json.loads(p.read_text()) for p in BATCH.glob('M*.result.json')]
        for report in reports:
            if report.get('account_id'):
                write(report['sample'] + '.verified-facts.json', collect(report['account_id']))
        recorded = {r['sample'] for r in reports}
        for _, current, correlation in LEDGER.batch_sends():
            if current not in recorded:
                account = correlation['account_id']
                write(f'{current}.interrupted-facts.json', collect(account))
                reports.append(dict(sample=current, outcome='environment_error', reason='driver interrupted after accepted messages; retain all sends and original facts'))
                recorded.add(current)
        write('verification.json', dict(collected_at=time.time(), samples=reports, budget=LEDGER.summary(),
            passed=len(reports) == 13 and all(r['outcome'] == 'pass' for r in reports)))
        return 0
    if (BATCH / 'run-started').exists():
        raise Rejected('batch execution is create-only; preserve prior samples')
    (BATCH / 'run-started').touch()
    wait_for(lambda: (BATCH / 'worker-ready').exists(), 180, 'dedicated Worker readiness')
    wait_for(lambda: httpx.get('http://gateway:8080/health/ready', timeout=5).status_code == 200, 60, 'dedicated API readiness')
    results = []
    for current in ('M1-1', 'M3-1'):
        if LEDGER.halt_reason():
            break
        results.append(run_sample(current))
        if results[-1]['outcome'] == 'uncertain':
            break
    pilot_ok = len(results) == 2 and all(r['outcome'] == 'pass' for r in results)
    if pilot_ok:
        by_name = {r['sample']: r for r in results}
        for current in ('M1-2', 'M1-3', 'M2-1', 'M2-2', 'M2-3', 'M3-2', 'M3-3', 'M4-1', 'M5-1', 'M6-1', 'M7-1'):
            if LEDGER.halt_reason():
                break
            baseline = by_name.get('M1-' + current[-1], {}) if current.startswith('M2') else {}
            parent = baseline.get('reuse')
            existing = by_name['M1-1'].get('reuse') if current == 'M5-1' else None
            if current.startswith('M2') and (baseline.get('outcome') != 'pass' or not parent):
                result = dict(sample=current, outcome='not_covered', reason='paired M1 did not produce an accepted baseline; no substitute task or model request sent')
                write(current + '.result.json', result)
            else:
                result = run_sample(current, parent, existing)
            results.append(result)
            by_name[current] = result
            if result['outcome'] in {'uncertain', 'environment_error'}:
                break
    counts = {}
    for scenario, planned in M['scenarios'].items():
        observed = [r for r in results if r['sample'].startswith(scenario + '-')]
        passed = sum(r['outcome'] == 'pass' for r in observed)
        counts[scenario] = dict(planned=planned, executed=len(observed), passed=passed,
            failed=sum(r['outcome'] == 'fail' for r in observed),
            environment_errors=sum(r['outcome'] == 'environment_error' for r in observed),
            uncertain=sum(r['outcome'] == 'uncertain' for r in observed),
            not_covered=planned - len(observed) + sum(r['outcome'] == 'not_covered' for r in observed),
            observed_pass_rate=passed / len(observed) if observed else None, planned_pass_rate=passed / planned)
    write('summary.json', dict(per_scenario=counts, pilot_passed=pilot_ok, planned=13, executed=len(results),
        outcomes={r['sample']: r['outcome'] for r in results}, budget=LEDGER.summary(),
        remaining='not executed: pilot or environmental/uncertain stop gate' if len(results) != 13 else None,
        halt_reason=LEDGER.halt_reason(),
        quality_review='independent manual review pending; basic checks do not prove factual accuracy',
        exit_satisfied=False))
    return 0 if len(results) == 13 and all(r['outcome'] == 'pass' for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
