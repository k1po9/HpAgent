"""Normal-product MVP driver; independent review gates every next paid stage."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

from s7.core import Ledger, Rejected
from s7.evidence import collect, judge, saved_html
from s7.scenarios import Product, collect_http_inputs, provider_events, wait_for, write

BATCH = Path('/batch')
M = json.loads((BATCH / 'manifest.json').read_text())
LEDGER = Ledger(Path('/campaign/ledger.sqlite'), M)


def browser(product, task, selected, *, revision=False):
    from playwright.sync_api import expect, sync_playwright
    outcome = {'passed': False}
    with sync_playwright() as p:
        app = p.chromium.launch(headless=True, executable_path=M['resources']['browser_executable'], args=['--no-sandbox'])
        context = app.new_context(viewport={'width': 1440, 'height': 1000})
        context.add_cookies([{'name': c.name, 'value': c.value, 'url': 'http://gateway:8080', 'httpOnly': True, 'sameSite': 'Lax'} for c in product.client.cookies.jar])
        context.tracing.start(screenshots=True, snapshots=True)
        page = context.new_page()
        try:
            # Saved file -> exact interactive Artifact, after leaving and reload.
            page.goto('http://gateway:8080/#/workspace?inspect=file:' + selected['node_id'])
            page.reload()
            page.get_by_role('button', name=re.compile('^打开交互成果 v')).click(timeout=20000)
            frame_element = page.get_by_title('Artifact 预览')
            expect(frame_element).to_be_visible()
            expect(frame_element).to_have_attribute('sandbox', 'allow-scripts')
            actual = frame_element.get_attribute('srcdoc')
            assert hashlib.sha256(actual.encode()).hexdigest() == selected['sha256']
            frame = frame_element.content_frame
            a, b = task['category_a'], task['category_b']
            cards_a = frame.locator('article').filter(has=frame.get_by_role('heading', name=re.compile(re.escape(a), re.I)))
            cards_b = frame.locator('article').filter(has=frame.get_by_role('heading', name=re.compile(re.escape(b), re.I)))
            first, other = cards_a.first, cards_b.first
            expect(first).to_be_visible()
            expect(other).to_be_visible()
            frame.get_by_role('button', name=a, exact=True).click()
            expect(first).to_be_visible()
            expect(other).to_be_hidden()
            frame.get_by_role('button', name=b, exact=True).click()
            expect(first).to_be_hidden()
            expect(other).to_be_visible()
            frame.get_by_role('button', name='全部', exact=True).click()
            expect(first).to_be_visible()
            expect(other).to_be_visible()
            if revision:
                search = frame.get_by_role('textbox', name='搜索条目', exact=True)
                search.fill(a)
                expect(first).to_be_visible()
                expect(other).to_be_hidden()
                search.fill('')
                expect(first).to_be_visible()
                expect(other).to_be_visible()
            page.screenshot(path=str(BATCH / f'{product.sample}.png'), full_page=True)
            outcome.update(passed=True, preview_sha256=selected['sha256'], browser_version=app.version)
        except Exception as exc:
            outcome['reason'] = type(exc).__name__ + ': ' + str(exc)[:500]
            page.screenshot(path=str(BATCH / f'{product.sample}.failed.png'), full_page=True)
        finally:
            context.tracing.stop(path=str(BATCH / f'{product.sample}.browser.zip'))
            app.close()
    return outcome


def snapshot(product):
    facts = collect(product.account)
    write(product.sample + '.facts.json', facts)
    collect_http_inputs(product, facts)
    return facts


def research(task):
    current = task['id']
    (BATCH / 'active-sample').write_text(current)
    product = Product(current)
    product.register()
    record = dict(sample=current, account_id=product.account, started_at=time.time(), outcome='fail')
    try:
        workspace = product.call('GET', '/api/v1/workspace')
        directory = product.call('POST', '/api/v1/workspace/directories', {'parent_id': workspace['root_id'], 'name': 'MVP-' + current})['node_id']
        cid = product.call('POST', '/api/v1/conversations', {'title': task['title']})['conversation']['conversation_id']
        prompt = M['prompt'].format(**task, directory=directory)
        sent = product.call('POST', f'/api/v1/conversations/{cid}/messages', dict(content=prompt, agent_strategy='react'))
        source, rid = sent['user_message']['message_id'], sent['run']['run_id']
        record.update(source_message_id=source, main_run_id=rid, conversation_id=cid, directory_id=directory)
        def accepted():
            matches = [w for w in product.call('GET', '/api/v1/works')['items'] if any(s['source_message_id'] == source for s in w['sources'])]
            if matches:
                if len(matches) != 1:
                    raise Rejected('duplicate Main acceptance')
                return matches[0]
            if product.call('GET', '/api/v1/runs/' + rid)['run']['status'] in {'failed', 'succeeded', 'cancelled'}:
                raise Rejected('Main ended without the requested Work')
        w = wait_for(accepted, 240, 'Main acceptance')
        wid = w['work_id']
        record['work_id'] = wid
        if w['requirement']['capability_key'] != 'generic_work' or w['active_coordinator_run_id']:
            raise Rejected('Main did not honor scheduled generic mandate preparation')
        # Ordinary resource and full-requirement revision commands implement the
        # user's explicit directory selection and switch from scheduled to now.
        product.mutate_work(wid, 'resources', dict(node_id=directory, operations=['list_metadata', 'create_child'], recursive=False))
        req = product.work(wid)['requirement']
        fields = ('objective', 'capability_key', 'spec', 'constraints', 'acceptance_criteria', 'completion_mode', 'timing', 'resource_requests', 'deliverable_policy')
        req = {k: req[k] for k in fields}
        req['timing'] = {'schema_version': 1, 'kind': 'immediate', 'timezone': 'UTC'}
        req['deliverable_policy'] = {'schema_version': 1, 'required': True, 'directory_id': directory}
        if not any(c['required'] and 'workspace_saved' in c['evidence_types'] for c in req['acceptance_criteria']):
            # Same save selection behavior as the task editor, never a DB patch.
            req['acceptance_criteria'].append({'id': 'mvp_user_save', 'required': True, 'evidence_types': ['workspace_saved']})
        product.mutate_work(wid, 'revisions', dict(requirement=req, change_reason='已选择并授权成果目录，现在立即执行'))
        # A real browser leaves the task page while the durable Work continues.
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            app = p.chromium.launch(executable_path=M['resources']['browser_executable'], args=['--no-sandbox'])
            context = app.new_context()
            context.add_cookies([{'name': c.name, 'value': c.value, 'url': 'http://gateway:8080'} for c in product.client.cookies.jar])
            page = context.new_page()
            page.goto('http://gateway:8080/#/tasks?work=' + wid)
            page.get_by_role('button', name='空间', exact=True).click(timeout=20000)
            app.close()
        def terminal():
            work = product.work(wid)
            if work['status'] == 'completed':
                return work
            if not work['active_coordinator_run_id'] and work['continuation']['kind'] in {'blocked', 'awaiting_input'}:
                raise Rejected('Work stopped for input/control: ' + json.dumps(work['continuation'], ensure_ascii=False))
        w = wait_for(terminal, M['limits']['scenario_seconds'], 'Work completion')
        facts = snapshot(product)
        saves = saved_html(facts, wid, directory)
        if len(saves) != 1:
            raise Rejected('one exact final checked and saved HTML required')
        saved = saves[0]
        selected = dict(version_id=saved['version']['artifact_version_id'], artifact_id=saved['version']['artifact_id'],
                        node_id=saved['node']['node_id'], sha256=saved['ref']['sha256'], work_id=wid)
        view = product.call('GET', '/api/v1/artifact-versions/' + selected['version_id'])['version']
        (BATCH / f'{current}.html').write_text(view['html'])
        selected['version_response'] = view
        record['selected'] = selected
        check = browser(product, task, selected)
        record['browser'] = check
        record['assertions'] = judge(facts, work=wid, source_message=source, scenario='M1',
            provider_events=provider_events(current), browser=check, main_run_id=rid, directory=directory)
        discovered = []
        with LEDGER.connect() as db:
            for (body,) in db.execute('SELECT body FROM events WHERE kind="source_discovered"'):
                item = json.loads(body)
                if item['sample'] == current:
                    discovered.extend(item['candidates'])
        record['assertions'].append(dict(id='normal_source_discovery', passed=bool(discovered) and all(c['provider'] != 'approved_public_documents' for c in discovered)))
        record['outcome'] = 'pass' if all(a['passed'] for a in record['assertions']) else 'fail'
        return product, record
    except BaseException as exc:
        record['reason'] = type(exc).__name__ + ': ' + str(exc)[:600]
        snapshot(product)
        return product, record
    finally:
        record['ended_at'] = time.time()
        record['budget'] = LEDGER.summary()
        write(current + '.result.json', record)


def revise(product, baseline, task):
    from playwright.sync_api import sync_playwright
    current = 'revision'
    product.sample = current
    (BATCH / 'active-sample').write_text(current)
    original = baseline['selected']
    record = dict(sample=current, account_id=product.account, outcome='fail', old_version_id=original['version_id'])
    try:
        with sync_playwright() as p:
            app = p.chromium.launch(executable_path=M['resources']['browser_executable'], args=['--no-sandbox'])
            context = app.new_context()
            context.add_cookies([{'name': c.name, 'value': c.value, 'url': 'http://gateway:8080'} for c in product.client.cookies.jar])
            page = context.new_page()
            page.goto('http://gateway:8080/#/workspace?inspect=file:' + original['node_id'])
            page.get_by_role('button', name=re.compile('^打开交互成果 v')).click(timeout=20000)
            panel = page.locator('.hp-artifact-inspector')
            panel.get_by_label('修改指令', exact=True).fill(M['revision_prompt'])
            with page.expect_response(lambda r: r.request.method == 'POST' and r.url.endswith('/versions')) as response:
                panel.get_by_role('button', name='生成新版本', exact=True).click()
            pending = response.value.json()['version']
            app.close()
        vid = pending['artifact_version_id']
        def finished():
            v = product.call('GET', '/api/v1/artifact-versions/' + vid)['version']
            if v['status'] == 'failed':
                raise Rejected('HTML revision failed: ' + str(v['failure']))
            return v if v['status'] == 'completed' else None
        updated = wait_for(finished, 360, 'revision')
        # Save this exact generated immutable output through the normal API.
        files = product.call('GET', '/api/v1/runs/' + updated['producing_run_id'] + '/published-files')['files']
        html_file = next(f for f in files if f['name'].endswith('.html'))
        node = product.call('POST', '/api/v1/workspace/files', dict(parent_id=baseline['directory_id'], file_id=html_file['file_id'], name='MVP-revised.html'))['node_id']
        selected = dict(version_id=vid, artifact_id=updated['artifact_id'], node_id=node, sha256=hashlib.sha256(updated['html'].encode()).hexdigest())
        (BATCH / 'revision.html').write_text(updated['html'])
        record['selected'] = selected
        record['browser'] = browser(product, task, selected, revision=True)
        snapshot(product)
        old = product.call('GET', '/api/v1/artifact-versions/' + original['version_id'])['version']
        events = provider_events(current)
        record['assertions'] = [dict(id='old_version_unchanged', passed=old == original['version_response']),
            dict(id='exact_successful_parent', passed=updated['parent_version_id'] == original['version_id']),
            dict(id='no_repeated_research', passed=bool(events) and all(e['phase'] != 'research_synthesis' for e in events)),
            dict(id='old_work_stays_completed', passed=product.work(original['work_id'])['status'] == 'completed'),
            dict(id='browser_save_reopen_search', passed=record['browser']['passed'])]
        record['outcome'] = 'pass' if all(a['passed'] for a in record['assertions']) else 'fail'
    except BaseException as exc:
        record['reason'] = type(exc).__name__ + ': ' + str(exc)[:600]
        snapshot(product)
    record['budget'] = LEDGER.summary()
    write('revision.result.json', record)
    return record


def review_gate(stage, result):
    if result['outcome'] != 'pass':
        LEDGER.halt(stage + ' failed; diagnose before a newly frozen bounded retry')
        return False
    marker = BATCH / (stage + '.review.json')
    (BATCH / 'awaiting-review').write_text(stage)
    deadline = time.monotonic() + 900
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(1)
    if not marker.exists():
        LEDGER.halt('independent review deadline')
        return False
    review = json.loads(marker.read_text())
    if review.get('outcome') != 'pass' or review.get('sha256') != result['selected']['sha256']:
        LEDGER.halt('independent content/visual review failed')
        return False
    return True


def main():
    if sys.argv[1] == 'verify':
        records = []
        for stage in M['scenarios']:
            path = BATCH / (stage + '.result.json')
            if not path.exists():
                continue
            record = json.loads(path.read_text())
            if record.get('account_id'):
                write(stage + '.verified-facts.json', collect(record['account_id']))
            review_path = BATCH / (stage + '.review.json')
            review = json.loads(review_path.read_text()) if review_path.exists() else {}
            records.append(dict(stage=stage, outcome=record['outcome'],
                review_passed=bool(review.get('outcome') == 'pass' and record.get('selected') and
                                   review.get('sha256') == record['selected']['sha256'])))
        write('mvp-verification.json', dict(collected_at=time.time(), stages=records,
            passed=len(records) == 3 and all(r['outcome'] == 'pass' and r['review_passed'] for r in records)
                   and not LEDGER.halt_reason(), budget=LEDGER.summary(), s7_1_passed=False))
        return
    if (BATCH / 'run-started').exists():
        raise Rejected('MVP batch is create-only')
    (BATCH / 'run-started').touch()
    records = []
    first, one = research(M['tasks'][0])
    records.append(one)
    if review_gate('research_1', one):
        changed = revise(first, one, M['tasks'][0])
        records.append(changed)
        if review_gate('revision', changed):
            second, two = research(M['tasks'][1])
            records.append(two)
            review_gate('research_2', two)
            second.client.close()
    first.client.close()
    write('mvp-summary.json', dict(results=records, passed=len(records) == 3 and not LEDGER.halt_reason(),
        budget=LEDGER.summary(), s7_1_passed=False))


if __name__ == '__main__':
    main()
