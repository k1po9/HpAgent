#!/usr/bin/env python3
"""S7-1 isolated acceptance. --help and preflight never read deployment secrets."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from s7.core import (
    BASE_URL,
    MODEL,
    SCENARIOS,
    UPGRADES,
    Ledger,
    Rejected,
    abandon_unknown,
    digest,
    upgrade_authorization,
    validate,
)

ROOT = Path(__file__).resolve().parents[2]
LABEL = 'hpagent.s7.batch'
CAMPAIGN = Path('/home/hp/.local/state/hpagent/s7-1-20261021')


def ledger(directory, m):
    return Ledger(CAMPAIGN / 'ledger.sqlite', m)


def command(args, *, cwd=ROOT, timeout=60, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, timeout=timeout)
    if result.returncode:
        # Docker/process errors may contain connection strings. Keep details private.
        raise Rejected(f'{args[0]} failed (exit {result.returncode}); inspect restricted process log')
    return result.stdout.decode().strip()


def fingerprint():
    paths = command(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z']).split('\0')
    files = {}
    for name in paths:
        p = ROOT / name
        if p.is_file():
            if p.is_symlink():
                raise Rejected('source symlink cannot escape frozen checkout')
            files[name] = hashlib.sha256(p.read_bytes()).hexdigest()
    migrations = {p: h for p, h in files.items() if p.startswith('persistence/migrations/')}
    runtime = {p: h for p, h in files.items() if p.startswith(('src/', 'config/prompts/', 'scripts/acceptance/'))}
    return dict(head=command(['git', 'rev-parse', 'HEAD']), tree_hash=digest(files),
                migration_hash=digest(migrations), runtime_hash=digest(runtime), files=files,
                migrations=migrations)


def exact_model_fields(path):
    """Never source a shell or interpolate unrelated deployment variables."""
    import shlex
    wanted = {'SILICONFLOW_FLAGSHIP_MODEL', 'SILICONFLOW_BASE_URL', 'SILICONFLOW_API_KEY'}
    values = {}
    with Path(path).open() as stream:
        for line in stream:
            key, sep, raw = line.strip().removeprefix('export ').partition('=')
            if sep and key in wanted:
                if key in values:
                    raise Rejected('duplicate authorized model variable')
                tokens = shlex.split(raw, comments=True)
                if len(tokens) != 1 or not tokens[0] or any(x in tokens[0] for x in ('$', '`', '\n', '\r')):
                    raise Rejected('authorized model variable requires a nonempty literal')
                values[key] = tokens[0]
    if set(values) != wanted:
        raise Rejected('missing authorized model variables')
    if values['SILICONFLOW_FLAGSHIP_MODEL'] != MODEL or values['SILICONFLOW_BASE_URL'].rstrip('/') != BASE_URL:
        raise Rejected('authorized model/base URL changed; report difference and refreeze before execution')
    return values


def image(name):
    return command(['docker', 'image', 'inspect', '--format', '{{.Id}}', name])


def create_manifest(directory, prior, port_offset=0, *, mvp=False):
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    CAMPAIGN.mkdir(mode=0o700, parents=True, exist_ok=True)
    token_limit = 1_000_000
    if (CAMPAIGN / 'ledger.sqlite').exists():
        with sqlite3.connect('file:' + str(CAMPAIGN / 'ledger.sqlite') + '?mode=ro', uri=True) as db:
            token_limit = db.execute('SELECT token_limit FROM authorization WHERE singleton=1').fetchone()[0]
    batch = 's7-' + secrets.token_hex(6)
    m = dict(schema_version=1, batch_id=batch, strategy='react', scenarios=SCENARIOS,
             code=fingerprint(), budget_mode='enforce', faults=['M3', 'M4', 'M5', 'M6'],
             disabled=['qq', 'email', 'mcp', 'shell', 'git', 'publishing', 'memory', 'embedding', 'rerank', 'fake_executor'],
             model=dict(provider='siliconflow', id=MODEL, base_url=BASE_URL, routes=['chat', 'fast', 'reasoning'],
                        fallbacks=[], auxiliary_models=[], stream=False, max_output_tokens=8192,
                        enable_thinking=False, artifact_read_seconds=240),
             limits=dict(batch_tokens=token_limit, prior_tokens=prior, request_tokens=131072,
                         scenario_tokens=320000, run_tokens=160000, work_tokens=240000,
                         requests=640, prior_requests=1, scenario_requests=24, request_seconds=240,
                         scenario_seconds=2400, batch_seconds=28800, output_bytes=1048576),
             resources=dict(internal_network=True, namespace='default', egress_tunnel_seconds=270,
                            ports={k: v + port_offset for k, v in dict(api=18381, postgres=15471, redis=16391, temporal=17241).items()},
                            python_image=image('hpagent/s2-test-python:local'),
                            postgres_image=image('postgres:16-alpine'), redis_image=image('redis:7-alpine'),
                            temporal_image=image('temporalio/auto-setup:1.26.2'),
                            node_image=image('node:22-bookworm-slim'), browser_path='/root/.cache/ms-playwright',
                            browser_executable='/ms-playwright/chromium-1234/chrome-linux64/chrome',
                            browser_version='151.0.7922.34'),
             sources=[dict(url='https://docs.python.org/3/library/asyncio-task.html',
                           locator='Task Groups / asyncio.gather / asyncio.shield',
                           facts=['TaskGroup awaits all tasks when exiting the context']),
                      dict(url='https://docs.python.org/3/library/asyncio-sync.html',
                           locator='Lock / Event / Semaphore',
                           facts=['Lock acquisition is fair']),
                      dict(url='https://docs.python.org/3/library/asyncio-queue.html',
                           locator='asyncio.Queue',
                           facts=['asyncio.Queue is a FIFO queue', 'asyncio.Queue is not thread safe'])],
             egress_hosts=['api.siliconflow.cn', 'docs.python.org'],
             prompt='这是一个需要后台继续完成的任务。请仅依据批准的公开 Python asyncio 技术文档：{sources}，整理任务编排和同步原语的比较说明（覆盖 TaskGroup、gather、Lock、Event、Queue），做成带“编排/同步/全部”内容筛选的单页，每个比较条目使用带标题的独立 article 卡片，条目按类别标注，使用名称恰为“编排”“同步”“全部”的三个筛选按钮；保留准确引用，不添加无依据事实。请保存到我将明确授权给该任务的“验收成果”目录（目录 ID：{directory}）。资料不足时说明缺口，不要编造。我离开页面后请继续。',
             revision_prompt='请把我明确选定的已完成 HTML 成果（版本 {version}，已附准确文件）另建后台任务修改：新增可访问名称为“搜索条目”的关键词搜索框，按卡片标题实时筛选。保留原有编排/同步/全部分类筛选、所有事实、引用和旧版本，不需要重新研究。将新版本保存到我将授予该新任务的“验收成果”目录 {directory}。',
             ordinary_prompt='普通问答：1 加 1 是多少？不要修改任何后台任务。',
             clarification='请按原目标在后台完成，使用已批准的公开文档与明确授予的目录权限，不外发，不增加研究主题。',
             browser_assertion='在正式 Artifact sandbox iframe 中按可访问名称切换“编排/同步/全部”，断言相应条目可见/隐藏并恢复。',
             recovery=dict(execution_lease_seconds=900, research_lease_seconds=30,
                           activity_recovery_seconds=1200, production_defaults=True),
             bindings={'M2-1': 'M1-1', 'M2-2': 'M1-2', 'M2-3': 'M1-3', 'M5-1_saved_baseline': 'M1-1'},
             discovery='curated approved public URLs; curated official-doc provider enabled by official_sources or public_web; real content fetched by production StaticWebContentProvider; no search service or fixture text',
             effective_governance=dict(account_day_tokens=800000, budget_rejection_account_day_tokens=1,
                 generic_work_run_tokens=160000, generic_work_run_input_tokens=128000,
                 generic_work_run_output_tokens=32000, work_tokens=240000, work_model_calls=32,
                 account_prompt_visibility='full_safe', source_discovery='approved_public_documents', tool_rag_enabled=False,
                 tool_selection_top_k=12, tool_schema_boundary='complete serialized schema; no total twelve-tool cap'),
             dispatch_guard_version=1,
             prior_usage_basis='11-token preparation probe only; all subsequent usage remains in the original campaign sends ledger.')
    if mvp:
        from s7.mvp import configure_manifest
        configure_manifest(m, image)
    validate(m)
    (directory / 'manifest.json').write_text(json.dumps(m, ensure_ascii=False, indent=2))
    ledger(directory, m)
    return m


def load(directory):
    return validate(json.loads((directory / 'manifest.json').read_text()))


def preflight(directory):
    m = load(directory)
    if fingerprint() != m['code']:
        raise Rejected('working tree changed since freeze; preserve old batch and create a new one')
    for port in m['resources']['ports'].values():
        with socket.socket() as sock:
            try:
                sock.bind(('127.0.0.1', port))
            except OSError as exc:
                raise Rejected(f'dedicated port {port} unavailable') from exc
    return m


def docker_owned(m, kind, suffix):
    identity = m['batch_id'] + '-' + suffix
    found = command(['docker', kind, 'inspect', '--format', '{{index .Config.Labels "' + LABEL + '"}}' if kind == 'container' else '{{index .Labels "' + LABEL + '"}}', identity])
    if found != m['batch_id']:
        raise Rejected('resource ownership mismatch; refuse mutation')
    return identity


def require_source_precheck(directory, manifest):
    if manifest.get('profile') != 'research-html-mvp':
        return
    path = directory / 'source-precheck.json'
    result = json.loads(path.read_text()) if path.exists() else {}
    if (result.get('gate_version') != 2 or result.get('passed') is not True
            or result.get('model_requests') != 0
            or result.get('code_tree_hash') != manifest['code']['tree_hash']
            or len(result.get('tasks', [])) < 2
            or not all(t.get('passed') is True for t in result.get('tasks', []))):
        raise Rejected('MVP requires a passing unpaid source gate for this frozen code before real model requests')


def source_precheck(directory):
    """Dedicated search/egress only; never read credentials or start a model Worker."""
    m = preflight(directory)
    if m.get('profile') != 'research-html-mvp' or (directory / 'source-precheck.json').exists():
        raise Rejected('source precheck requires a fresh MVP batch')
    prefix = m['batch_id'] + '-precheck'
    internal, external = prefix + '-private', prefix + '-external'
    settings = directory / 'search-settings.yml'
    settings.write_text(m['search_settings'])
    created = []
    networks = []
    try:
        for network, flags in ((internal, ['--internal']), (external, [])):
            command(['docker', 'network', 'create', *flags, '--label', f'{LABEL}={m["batch_id"]}', network])
            networks.append(network)
        def start(suffix, img, options, args):
            name = prefix + '-' + suffix
            command(['docker', 'create', '--name', name, '--label', f'{LABEL}={m["batch_id"]}',
                '--network', internal, '--network-alias', suffix, *options, img, *args])
            created.append(name)
            command(['docker', 'network', 'connect', external, name])
            command(['docker', 'start', name])
        start('searxng', m['resources']['search_image'], ['-v', f'{settings}:/etc/searxng/settings.yml:ro'], [])
        start('egress', m['resources']['python_image'], ['--entrypoint', '', '-v', f'{ROOT}:/code:ro', '-v', f'{directory}:/batch'],
            ['python', '/code/scripts/acceptance/s7/proxy.py'])
        probe = ['docker', 'run', '--rm', '--network', internal, '--entrypoint', 'python',
            '-v', f'{ROOT}:/code:ro', '-v', f'{directory}:/batch', '-w', '/code',
            '-e', 'PYTHONPATH=/code/src:/code/scripts/acceptance',
            '-e', 'HTTPS_PROXY=http://egress:8888', '-e', 'HTTP_PROXY=http://egress:8888',
            '-e', 'NO_PROXY=searxng,localhost,127.0.0.1', m['resources']['python_image']]
        command([*probe, '-c', 'import time,httpx; c=httpx.Client(); end=time.monotonic()+45\nwhile time.monotonic()<end:\n try:\n  c.get("http://searxng:8080/healthz").raise_for_status(); break\n except Exception: time.sleep(1)\nelse: raise RuntimeError("search startup deadline")'], timeout=55)
        result = subprocess.run([*probe, '/code/scripts/acceptance/s7/mvp.py', 'source-precheck'], capture_output=True, timeout=240)
        (directory / 'source-precheck.log').write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise Rejected('source precheck failed; preserve diagnostics and stop before model requests')
    finally:
        for name in reversed(created):
            logs = subprocess.run(['docker', 'logs', name], capture_output=True)
            (directory / (name.removeprefix(prefix + '-') + '.log')).write_bytes(logs.stdout + logs.stderr)
            command(['docker', 'rm', '-f', name])
        for network in reversed(networks):
            command(['docker', 'network', 'rm', network])
    return m


def prepare(directory, model_env, *, fixture_services=False):
    m = preflight(directory)
    if (directory / 'ownership.json').exists():
        raise Rejected('prepare is create-only; never adopt pre-existing resources')
    values = {} if fixture_services else exact_model_fields(model_env)
    if not fixture_services:
        (directory / 'model-authorization-check.json').write_text(json.dumps({
            'provider': 'siliconflow', 'model': values['SILICONFLOW_FLAGSHIP_MODEL'],
            'base_url': values['SILICONFLOW_BASE_URL'], 'key_present': bool(values['SILICONFLOW_API_KEY'])}))
    checkout = directory / 'checkout'
    checkout.mkdir()
    for name in m['code']['files']:
        target = checkout / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    (checkout / 'persistence/docker-init').chmod(0o755)
    shutil.copytree(checkout / 'config/prompts', directory / 'prompts')
    # Freeze runtime versions without importing or starting provider clients.
    versions = command(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python', m['resources']['python_image'], '-c',
                        'import importlib.metadata,json; print(json.dumps({n:importlib.metadata.version(n) for n in ("httpx","temporalio","psycopg")}))'])
    (directory / 'sdk-versions.json').write_text(versions)
    key = secrets.token_hex(20)
    environment = {'MIGRATION_DATABASE_URL': f'postgresql://hpagent_migrate:{key}@postgres:5432/hpagent',
                   'APP_DATABASE_URL': f'postgresql://hpagent_api:{key}@postgres:5432/hpagent',
                   'WORKER_DATABASE_URL': f'postgresql://hpagent_worker:{key}@postgres:5432/hpagent',
                   'REDIS_URL': 'redis://redis:6379/0', 'TEMPORAL_HOST': 'temporal:7233',
                   'HPAGENT_MIGRATIONS_DIR': '/code/persistence/migrations',
                   'TEMPORAL_NAMESPACE': 'default', 'TEMPORAL_TEST_NAMESPACE': 'default',
                   'FILE_STORE_ROOT': '/batch/files', 'RUN_SCOPE_ROOT': '/batch/scopes', 'FILE_RUN_ROOT': '/batch/scopes',
                   'WORKSPACE_ROOT': '/batch/workspaces', 'HPAGENT_CONFIG_PATH': '/batch/app.json',
                   'HPAGENT_MODELS_PATH': '/batch/models.json', 'PYTHONPATH': '/code/src:/code/scripts/acceptance',
                   'ARTIFACT_MODEL_READ_TIMEOUT_SECONDS': str(m['model']['artifact_read_seconds']),
                   'RUN_BUDGET_MODE': 'enforce', 'WEB_FAKE_EXECUTOR_ENABLED': 'false',
                   'WEB_FILE_UPLOAD_ENABLED': 'true', 'FILE_UPLOAD_ENABLED': 'true',
                   'WEB_FILE_TRANSFORM_ENABLED': 'false', 'WEB_FILE_SHELL_ENABLED': 'false',
                   'FILE_SHELL_ENABLED': 'false', 'WEB_COOKIE_SECURE': 'false',
                   'WEB_PUBLIC_ORIGIN': f'http://127.0.0.1:{m["resources"]["ports"]["api"]}',
                   'WEB_API_PORT': '8080', 'LOG_DIR': '/batch/logs',
                   'HTTPS_PROXY': 'http://egress:8888', 'HTTP_PROXY': 'http://egress:8888',
                   'NO_PROXY': 'localhost,127.0.0.1,postgres,redis,temporal,api,gateway,searxng',
                   'SILICONFLOW_API_KEY': values.get('SILICONFLOW_API_KEY', '')}
    (directory / 'private.env').write_text('\n'.join(f'{k}={v}' for k, v in environment.items() if k != 'MIGRATION_DATABASE_URL') + '\n')
    os.chmod(directory / 'private.env', 0o600)
    if fixture_services:
        (directory / 'test.env').write_text('\n'.join(f'{k}={v}' for k, v in environment.items()
            if k not in {'SILICONFLOW_API_KEY', 'HTTPS_PROXY', 'HTTP_PROXY'}) + '\n')
    (directory / 'service-mode.json').write_text(json.dumps({'fixture_services': fixture_services}))
    models = {'providers': {'siliconflow': {'base_url': BASE_URL, 'api_key': '${SILICONFLOW_API_KEY}', 'api_format': 'openai'}},
              **{route: [dict(provider='siliconflow', model=MODEL, max_tokens=8192, timeout=90,
                              extra_body={'enable_thinking': False})] for route in ('chat', 'fast', 'reasoning')},
              'embedding': [], 'image': [], 'tool_rag': {'enabled': False, 'top_k': 12}, 'mcp': {'auto_connect': False},
              'skills': {'enabled': False}, 'channel_overrides': {'web': {'max_tokens': 8192, 'timeout': 90, 'stream': False}}}
    (directory / 'models.json').write_text(json.dumps(models))
    (directory / 'app.json').write_text(json.dumps(dict(channels={'enabled': []}, hindsight={'enabled': False},
         sandbox={'native_tools_enabled': False, 'nsjail_enabled': False},
         workspace={'root': '/batch/workspaces', 'workspace_isolation_mode': 'single_process_account_lock'},
         temporal={'host': 'temporal:7233'}, redis={'url': 'redis://redis:6379/0'},
         research={'max_sources': 3, 'max_fetches': 3, 'max_iterations': 3},
         agent={'max_tool_turns': 16, 'tool_result_summary_enabled': False})))
    # Ownership is recorded before mutations, so interrupted preparation is cleanable.
    ownership = {'batch_id': m['batch_id'], 'containers': [], 'networks': []}
    def record():
        (directory / 'ownership.json').write_text(json.dumps(ownership))
    record()
    network = m['batch_id'] + '-private'
    command(['docker', 'network', 'create', '--internal', '--label', f'{LABEL}={m["batch_id"]}', network])
    ownership['networks'].append(network)
    record()
    external = m['batch_id'] + '-egress'
    if not fixture_services:
        command(['docker', 'network', 'create', '--label', f'{LABEL}={m["batch_id"]}', external])
        ownership['networks'].append(external)
        record()
    def start(suffix, img, args, options=(), env=()):
        name = m['batch_id'] + '-' + suffix
        command(['docker', 'create', '--name', name, '--label', f'{LABEL}={m["batch_id"]}',
                 '--network', network, '--network-alias', suffix, *options,
                 *[v for pair in env for v in ('-e', pair)], img, *args])
        ownership['containers'].append(name)
        record()
        command(['docker', 'start', name])
    r = m['resources']
    start('postgres', r['postgres_image'], [],
          options=['-v', f'{checkout}/persistence/docker-init:/docker-entrypoint-initdb.d:ro'],
          env=['POSTGRES_USER=hpagent_migrate', f'POSTGRES_PASSWORD={key}', 'POSTGRES_DB=hpagent',
               f'HPAGENT_API_PASSWORD={key}', f'HPAGENT_WORKER_PASSWORD={key}'])
    start('redis', r['redis_image'], [])
    start('temporal-db', r['postgres_image'], [], env=['POSTGRES_USER=temporal', f'POSTGRES_PASSWORD={key}', 'POSTGRES_DB=temporal'])
    start('temporal', r['temporal_image'], [],
          env=['DB=postgres12', 'DB_PORT=5432', 'POSTGRES_USER=temporal', f'POSTGRES_PWD={key}',
               'POSTGRES_SEEDS=temporal-db', 'SKIP_ADD_CUSTOM_SEARCH_ATTRIBUTES=true'])
    if not fixture_services:
        start('egress', r['python_image'], ['python', '/code/scripts/acceptance/s7/proxy.py'],
              options=['--entrypoint', '', '-v', f'{checkout}:/code:ro', '-v', f'{directory}:/batch'])
        command(['docker', 'network', 'connect', external, m['batch_id'] + '-egress'])
    if m.get('profile') == 'research-html-mvp' and not fixture_services:
        settings = directory / 'search-settings.yml'
        settings.write_text(m['search_settings'])
        start('searxng', r['search_image'], [], options=['-v', f'{settings}:/etc/searxng/settings.yml:ro'],
              env=['SEARXNG_SECRET=' + secrets.token_hex(24)])
        command(['docker', 'network', 'connect', external, m['batch_id'] + '-searxng'])
    deadline = time.monotonic() + 120
    while True:
        try:
            command(['docker', 'exec', m['batch_id'] + '-postgres', 'pg_isready', '-h', '127.0.0.1', '-U', 'hpagent_migrate'], timeout=10)
            break
        except Rejected:
            if time.monotonic() > deadline:
                raise Rejected('dedicated postgres startup deadline exceeded')
            time.sleep(1)
    run_process(directory, m, 'migrate', ['python', '-m', 'persistence.migrate'], foreground=True,
                extra_env={'APP_DATABASE_URL': environment['MIGRATION_DATABASE_URL']})
    if fixture_services:
        return m
    run_process(directory, m, 'provision', ['python', '/code/scripts/acceptance/s7/evidence.py'], foreground=True,
                extra_env={'MIGRATION_DATABASE_URL': environment['MIGRATION_DATABASE_URL']})
    (checkout / 'web/node_modules').mkdir(exist_ok=True)
    build = subprocess.run(['docker', 'run', '--rm', '--network', 'none',
        '-v', f'{checkout}/web:/web:ro', '-v', f'{ROOT}/web/node_modules:/web/node_modules:ro',
        '-v', f'{directory}:/batch', '-w', '/web', m['resources']['node_image'],
        'node', 'node_modules/vite/bin/vite.js', 'build', '--configLoader', 'runner', '--outDir', '/batch/frontend'],
        capture_output=True, timeout=180)
    (directory / 'frontend-build.log').write_bytes(build.stdout + build.stderr)
    if build.returncode:
        raise Rejected('frozen frontend build failed')
    run_process(directory, m, 'api', ['python', '/code/scripts/acceptance/s7/api.py'])
    run_process(directory, m, 'gateway', ['python', '/code/scripts/acceptance/s7/gateway.py'], ports=True)
    run_process(directory, m, 'worker', ['python', '/code/scripts/acceptance/s7/worker.py'])
    deadline = time.monotonic() + 150
    while not (directory / 'worker-ready').exists():
        if command(['docker', 'inspect', '--format', '{{.State.Running}}', m['batch_id'] + '-worker']) != 'true':
            logs = subprocess.run(['docker', 'logs', m['batch_id'] + '-worker'], capture_output=True)
            (directory / 'worker-startup.log').write_bytes(logs.stdout + logs.stderr)
            raise Rejected('dedicated Worker exited before readiness; inspect restricted worker-startup.log')
        if time.monotonic() > deadline:
            raise Rejected('dedicated Worker readiness deadline exceeded')
        time.sleep(0.5)
    return m


def run_process(directory, m, name, args, *, foreground=False, ports=False, extra_env=None):
    identity = m['batch_id'] + '-' + name
    if name == 'verify':
        identity += '-' + secrets.token_hex(3)
    ownership = json.loads((directory / 'ownership.json').read_text())
    source = ROOT if name == 'verify' else directory / 'checkout'
    if name == 'verify':
        (directory / 'verifier-code.json').write_text(json.dumps(fingerprint()))
    options = ['docker', 'create', '--name', identity, '--label', f'{LABEL}={m["batch_id"]}',
               '--network', m['batch_id'] + '-private', '--network-alias', name, '--entrypoint', '',
               '-w', '/code', '-v', f'{source}:/code:ro', '-v', f'{directory}:/batch', '-v', f'{CAMPAIGN}:/campaign',
               '--env-file', str(directory / 'private.env')]
    if name in {'run', 'verify'}:
        options += ['-v', f'{m["resources"]["browser_path"]}:/ms-playwright:ro']
    if ports:
        options += ['-p', f'127.0.0.1:{m["resources"]["ports"]["api"]}:8080']
    for k, v in (extra_env or {}).items():
        options += ['-e', f'{k}={v}']
    command([*options, m['resources']['python_image'], *args])
    ownership['containers'].append(identity)
    (directory / 'ownership.json').write_text(json.dumps(ownership))
    command(['docker', 'start', identity])
    if foreground:
        if name == 'run':
            deadline = time.monotonic() + m['limits']['batch_seconds']
            while command(['docker', 'inspect', '--format', '{{.State.Running}}', identity]) == 'true':
                control = directory / 'restart-worker.json'
                if control.exists():
                    target = json.loads(control.read_text())
                    if target.get('sample') != 'M4-1':
                        raise Rejected('invalid Worker restart controller target')
                    worker = docker_owned(m, 'container', 'worker')
                    command(['docker', 'kill', '--signal', 'KILL', worker])
                    (directory / 'M4-1.release').touch()
                    command(['docker', 'start', worker])
                    control.rename(directory / 'restart-worker-consumed.json')
                if time.monotonic() > deadline:
                    command(['docker', 'stop', '--time', '10', identity])
                    raise Rejected('batch deadline reached; no additional samples')
                time.sleep(0.5)
        status = command(['docker', 'wait', identity], timeout=180)
        logs = subprocess.run(['docker', 'logs', identity], capture_output=True)
        (directory / f'{name}.log').write_bytes(logs.stdout + logs.stderr)
        if status != '0':
            if name == 'run':
                ledger(directory, m).halt('driver returned nonzero; preserve samples and reconcile before another batch')
            raise Rejected(f'{name} failed; inspect restricted {name}.log')


def cleanup(directory):
    m = load(directory)
    accounting = ledger(directory, m)
    accounting.halt('cleanup requested')
    deadline = time.monotonic() + m['limits']['request_seconds'] + 10
    while True:
        with accounting.connect() as db:
            active = db.execute('SELECT 1 FROM sends WHERE batch_id=? AND state="sending"', (m['batch_id'],)).fetchone()
        if not active or time.monotonic() >= deadline:
            break
        time.sleep(0.5)
    with accounting.connect() as db:
        db.execute('UPDATE sends SET state="uncertain" WHERE batch_id=? AND state="sending"', (m['batch_id'],))
    state = json.loads((directory / 'ownership.json').read_text())
    for identity in reversed(state['containers']):
        suffix = identity.removeprefix(m['batch_id'] + '-')
        docker_owned(m, 'container', suffix)
        logs = subprocess.run(['docker', 'logs', identity], capture_output=True)
        (directory / f'{suffix}.log').write_bytes(logs.stdout + logs.stderr)
        command(['docker', 'rm', '-f', '-v', identity])
    for identity in reversed(state['networks']):
        suffix = identity.removeprefix(m['batch_id'] + '-')
        docker_owned(m, 'network', suffix)
        command(['docker', 'network', 'rm', identity])
    (directory / 'private.env').unlink(missing_ok=True)
    (directory / 'cleaned.json').write_text(json.dumps({'at': time.time(), 'budget': ledger(directory, m).summary()}))


def main(argv=None):
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase', choices=['freeze', 'preflight', 'source-precheck', 'prepare', 'run', 'verify', 'cleanup', 'upgrade-authorization', 'abandon-unknown'])
    p.add_argument('--output', type=Path, required=True, help='new restricted batch directory outside checkout')
    p.add_argument('--profile', choices=['s7-1', 'research-html-mvp'], default='s7-1')
    p.add_argument('--port-offset', type=int, default=0, help='freeze only: offset the explicit dedicated host ports')
    p.add_argument('--send-id', default=None)
    p.add_argument('--decision-id', default=None)
    p.add_argument('--authorization-id', default=None, help='explicit offline cumulative authorization upgrade identity')
    p.add_argument('--prior-tokens', type=int, default=11)
    p.add_argument('--model-env', type=Path, default=Path('/home/hp/deploy/hpagent-beta/.env'))
    p.add_argument('--fixture-services', action='store_true', help='prepare only: fresh PG/Redis/Temporal without secrets, egress, API or Worker; for isolated regression')
    p.add_argument('--execute-real-model', action='store_true')
    p.add_argument('--allow-faults', nargs='*', default=[])
    args = p.parse_args(argv)
    directory = args.output.resolve()
    if directory.is_relative_to(ROOT) or not args.output.is_absolute():
        p.error('output must be an absolute directory outside the checkout')
    try:
        if args.phase == 'upgrade-authorization':
            old, additional = UPGRADES.get(args.authorization_id, (0, 0))
            changed = upgrade_authorization(CAMPAIGN / 'ledger.sqlite', args.authorization_id, old, additional)
            print(json.dumps({'authorization_id': args.authorization_id, 'token_limit': old + additional, 'changed': changed}))
            return 0
        if args.phase == 'abandon-unknown':
            changed = abandon_unknown(CAMPAIGN / 'ledger.sqlite', args.send_id, args.decision_id)
            print(json.dumps({'changed': changed, 'held_retained': True, 'resend': False}))
            return 0
        if args.phase == 'freeze':
            m = create_manifest(directory, args.prior_tokens, args.port_offset, mvp=args.profile == 'research-html-mvp')
        elif args.phase == 'preflight':
            m = preflight(directory)
        elif args.phase == 'source-precheck':
            m = source_precheck(directory)
        elif args.phase == 'prepare':
            m = prepare(directory, args.model_env, fixture_services=args.fixture_services)
        else:
            m = load(directory)
            if args.phase == 'cleanup':
                cleanup(directory)
            elif args.phase in ('run', 'verify'):
                if args.phase == 'run' and (not args.execute_real_model or set(args.allow_faults) != set(m['faults'])):
                    raise Rejected('run requires explicit real-model option and frozen fault allowlist')
                if args.phase == 'run' and json.loads((directory / 'service-mode.json').read_text())['fixture_services']:
                    raise Rejected('fixture-only resources cannot execute real-model scenarios')
                if args.phase == 'run':
                    require_source_precheck(directory, m)
                if args.phase == 'run' and fingerprint() != m['code']:
                    raise Rejected('code changed after freeze')
                run_process(directory, m, args.phase, ['python', '/code/scripts/acceptance/s7/mvp.py' if m.get('profile') == 'research-html-mvp' else '/code/scripts/acceptance/s7/scenarios.py', args.phase], foreground=True)
        print(json.dumps({'phase': args.phase, 'batch_id': m['batch_id'], 'budget': ledger(directory, m).summary()}, ensure_ascii=False))
        return 0
    except (Rejected, OSError, subprocess.TimeoutExpired) as exc:
        print(f'S7-1 refused: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
