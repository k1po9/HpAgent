"""Run only W3-E-owned containers; no existing application services are changed."""
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from temporalio.client import Client
from temporalio.api.workflowservice.v1 import DescribeTaskQueueRequest
from temporalio.api.taskqueue.v1 import TaskQueue
from temporalio.api.enums.v1 import TaskQueueType

ROOT = Path.cwd()
OUT = ROOT / 'artifacts/architecture-audit/phase3'
PREFIX = 'hpagent-w3e-' + uuid4().hex[:8]
containers = []

def docker(*args, check=True):
    result = subprocess.run(['docker', *args], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if check and result.returncode:
        raise RuntimeError(result.stdout)
    return result.stdout.strip()

def start(role, image, *args):
    name = PREFIX + '-' + role
    containers.append(name)
    docker('run', '-d', '--name', name, '--network', PREFIX, '--network-alias', role, *args, image)
    return name

def port(name, number):
    return int(docker('port', name, str(number)).rsplit(':', 1)[1])

def until(fn, timeout=90):
    deadline = time.monotonic() + timeout
    error = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except Exception as exc:
            error = exc
        time.sleep(1)
    raise RuntimeError(f'readiness timeout: {error}')

async def pollers(address):
    client = await Client.connect(address)
    expected = {
        'hpagent-web-lifecycle': [TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY],
        'hpagent-web-agent': [TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY],
        'hpagent-task-queue': [TaskQueueType.TASK_QUEUE_TYPE_WORKFLOW, TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY],
        'hpagent-document': [TaskQueueType.TASK_QUEUE_TYPE_ACTIVITY],
    }
    result = {}
    for queue, types in expected.items():
        for kind in types:
            response = await client.workflow_service.describe_task_queue(DescribeTaskQueueRequest(
                namespace='default', task_queue=TaskQueue(name=queue), task_queue_type=kind))
            result[f'{queue}:{kind}'] = len(response.pollers)
    return result if all(result.values()) else None

try:
    for name in ('api', 'migrate', 'worker', 'document'):
        image = f'hpagent-w3e-{name}:closure'
        docker('image', 'inspect', image)
        print(name, docker('run', '--rm', '--entrypoint', 'python', image, '-m', 'pip', 'check'), flush=True)
    docker('network', 'create', PREFIX)
    pg = start('pg', 'postgres:16-alpine', '-e', 'POSTGRES_PASSWORD=w3e_fixture', '-p', '127.0.0.1::5432')
    host_dsn = f'postgresql://postgres:w3e_fixture@127.0.0.1:{port(pg,5432)}/postgres'
    def roles():
        with psycopg.connect(host_dsn, autocommit=True) as conn:
            conn.execute("CREATE ROLE hpagent_api LOGIN PASSWORD 'w3e_fixture'")
            conn.execute("CREATE ROLE hpagent_worker LOGIN PASSWORD 'w3e_fixture'")
        return True
    until(roles)
    redis = start('redis', 'redis:7-alpine')
    temp = start('temporal', 'temporalio/auto-setup:1.26.2', '-p', '127.0.0.1::7233',
                 '-e', 'DB=postgres12', '-e', 'DB_PORT=5432', '-e', 'POSTGRES_USER=postgres',
                 '-e', 'POSTGRES_PWD=w3e_fixture', '-e', 'POSTGRES_SEEDS=pg', '-e', 'BIND_ON_IP=0.0.0.0')
    print(docker('run', '--rm', '--network', PREFIX, '-e',
                 'APP_DATABASE_URL=postgresql://postgres:w3e_fixture@pg:5432/postgres', 'hpagent-w3e-migrate:closure'), flush=True)
    with psycopg.connect(host_dsn) as conn:
        versions = [r[0] for r in conn.execute('SELECT version FROM hpagent.schema_migrations ORDER BY version')]
        assert versions == sorted(p.name for p in (ROOT/'persistence/migrations').glob('*.sql'))
        print('Fresh image applied all', len(versions), 'migrations', flush=True)
    until(lambda: 'SERVING' in docker('exec', temp, 'tctl', '--address', 'localhost:7233', 'cluster', 'health', check=False))
    with tempfile.TemporaryDirectory(prefix='w3e-config-') as directory:
        config = Path(directory)
        (config/'config.yaml').write_text('''channels: {enabled: []}
temporal: {host: "temporal:7233"}
redis: {url: "redis://redis:6379/0"}
hindsight: {enabled: false}
scheduler: {enabled: false}
workspace: {root: "/tmp/w3e-workspace", workspace_isolation_mode: "single_process_account_lock"}
''')
        (config/'models.yaml').write_text('''providers:
  fixture: {base_url: "http://127.0.0.1:9/v1", api_key: "fixture", api_format: "openai"}
chat: [{provider: fixture, model: fixture}]
tool_rag: {enabled: false}
mcp: {auto_connect: false}
skills: {enabled: false}
''')
        common = ['-e', 'WORKER_DATABASE_URL=postgresql://hpagent_worker:w3e_fixture@pg:5432/postgres', '-e', 'TEMPORAL_HOST=temporal:7233']
        worker = start('worker', 'hpagent-w3e-worker:closure', *common, '-e', 'QQ_BINDING_CODE_PEPPER=w3e-fixture-pepper',
                       '-v', str(config)+':/app/config:ro')
        (config/'file-store').mkdir()
        document = start('document', 'hpagent-w3e-document:closure', *common,
                         '-v', str(config/'file-store')+':/var/lib/hpagent/file-store:ro')
        api = start('api', 'hpagent-w3e-api:closure', '-p', '127.0.0.1::8080', '-e',
                    'APP_DATABASE_URL=postgresql://hpagent_api:w3e_fixture@pg:5432/postgres', '-e', 'REDIS_URL=redis://redis:6379/0')
        import urllib.request
        until(lambda: json.load(urllib.request.urlopen(f'http://127.0.0.1:{port(api,8080)}/health/ready', timeout=3))['status']=='ready')
        print('API /health/ready PASS', flush=True)
        actual = until(lambda: asyncio.run(pollers(f'127.0.0.1:{port(temp,7233)}')), timeout=90)
        print('Live Temporal queue pollers:', json.dumps(actual), flush=True)
        for name in (worker, document, api):
            assert docker('inspect', '--format', '{{.State.Running}}', name)=='true'
        print('PASS fresh API/migrate/worker/document image installation and production entrypoint startup', flush=True)
finally:
    for name in reversed(containers):
        (OUT / (name.rsplit('-',1)[1] + '_W3_E_install_container.txt')).write_text(docker('logs', name, check=False))
        docker('rm', '-f', '-v', name, check=False)
    docker('network', 'rm', PREFIX, check=False)
