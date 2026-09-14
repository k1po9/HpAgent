"""Run W1-B checks in a disposable PG container and isolated Temporal namespace.

Usage: .venv/bin/python scripts/verify_w1b_contracts.py [pytest paths/options]
Requires Docker's postgres:16-alpine image and a Temporal server (default localhost).
Never points destructive persistence fixtures at a developer's application DB.
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from google.protobuf.duration_pb2 import Duration
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import Client

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TESTS = [
    'test/web_persistence/test_agent_segments.py',
    'test/web_persistence/test_agent_segment_temporal.py',
    'test/test_agent_segment_replay.py',
    'test/test_agent_source_contract.py', 'test/test_durable_agent_contract.py',
    'test/test_durable_agent_hardening.py', 'test/test_trace_events.py',
    'test/test_tool_execution_approval_temporal.py', 'test/test_durable_agent_temporal_integration.py',
    'test/test_durable_agent_worker_kill.py', 'test/web_persistence/test_durable_agent_hardening.py',
    'test/web_persistence/test_durable_agent_activity_worker_kill.py',
    'test/web_persistence/test_f4_2_approval_temporal.py',
    'test/test_action_result_semantics.py', 'test/test_web_outbox_recovery.py',
    'test/test_web_temporal_contract.py', 'test/web_persistence/test_persistent_web_files.py',
]


async def namespace_action(host, namespace, *, register):
    client = await Client.connect(host, namespace=namespace)
    if register:
        await client.workflow_service.register_namespace(RegisterNamespaceRequest(
            namespace=namespace, workflow_execution_retention_period=Duration(seconds=86400)))
    else:
        async for item in client.list_workflows(query='ExecutionStatus = "Running"'):
            await client.get_workflow_handle(item.id).terminate('W1-B test fixture cleanup')


def main():
    name = 'hpagent-w1b-test-' + uuid4().hex[:10]
    host = os.getenv('TEMPORAL_HOST', '127.0.0.1:7233')
    registered = False
    subprocess.run(['docker', 'run', '--rm', '-d', '--name', name,
                    '-e', 'POSTGRES_PASSWORD=w1b_test', '-p', '127.0.0.1::5432',
                    'postgres:16-alpine'], check=True, stdout=subprocess.DEVNULL)
    try:
        address = subprocess.check_output(['docker', 'port', name, '5432'], text=True).strip()
        port = address.rsplit(':', 1)[1]
        url = f'postgresql://postgres:w1b_test@127.0.0.1:{port}/postgres'
        for _ in range(40):
            try:
                with psycopg.connect(url, autocommit=True) as conn:
                    conn.execute("CREATE ROLE hpagent_api LOGIN PASSWORD 'w1b_test'")
                    conn.execute("CREATE ROLE hpagent_worker LOGIN PASSWORD 'w1b_test'")
                break
            except psycopg.OperationalError:
                time.sleep(.5)
        else:
            raise RuntimeError('test PostgreSQL did not start')
        asyncio.run(namespace_action(host, name, register=True))
        registered = True
        env = dict(os.environ, MIGRATION_DATABASE_URL=url,
                   APP_DATABASE_URL=f'postgresql://hpagent_api:w1b_test@127.0.0.1:{port}/postgres',
                   WORKER_DATABASE_URL=f'postgresql://hpagent_worker:w1b_test@127.0.0.1:{port}/postgres',
                   TEMPORAL_HOST=host, TEMPORAL_NAMESPACE=name)
        print('Isolated PG / Temporal fixtures:', name, flush=True)
        return subprocess.run([sys.executable, '-m', 'pytest', '-q', '--tb=short',
                               *(sys.argv[1:] or DEFAULT_TESTS)], env=env, cwd=ROOT).returncode
    finally:
        try:
            if registered:
                asyncio.run(namespace_action(host, name, register=False))
        finally:
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, check=False)


if __name__ == '__main__':
    raise SystemExit(main())
