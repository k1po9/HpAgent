"""Compile Compose with synthetic inputs; never contact or start its services."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[2]


class RuntimeEnvironmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('docker'):
            raise unittest.SkipTest('Docker Compose CLI is required for static configuration checks')
        result = subprocess.run(['docker', 'compose', 'version'], capture_output=True, timeout=10)
        if result.returncode:
            raise unittest.SkipTest('Docker Compose plugin is required for static configuration checks')

    def compile(self, overrides=None):
        values = {'PATH': os.environ.get('PATH', '/usr/bin:/bin')}
        values.update({key: value for key, value in dotenv_values(
            ROOT / '.env.example', interpolate=False).items() if value is not None})
        for name in ('HPAGENT_MIGRATE_PASSWORD', 'HPAGENT_API_PASSWORD', 'HPAGENT_WORKER_PASSWORD',
                     'TEMPORAL_DB_PASSWORD', 'HINDSIGHT_DB_PASSWORD', 'WEB_CURSOR_SECRET',
                     'WEB_SESSION_TOKEN_PEPPER', 'WEB_CSRF_SIGNING_KEY', 'QQ_BINDING_CODE_PEPPER'):
            values[name] = 'fixture-only-material-00000000000000000000'
        values.update(overrides or {})
        result = subprocess.run([
            'docker', 'compose', '--env-file', '/dev/null', '-f', str(ROOT / 'docker-compose.yaml'),
            '--profile', '*', 'config', '--format', 'json',
        ], env=values, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, 'Compose static configuration failed; values withheld')
        return json.loads(result.stdout)['services']

    def test_optional_worker_and_shared_parameters_reach_their_consumers(self):
        shared = {'WORK_ACCOUNT_ACCEPT_LIMIT': '17'}
        worker = {
            'WORK_ACCOUNT_SUBAGENTS': '2', 'WORK_GLOBAL_SUBAGENTS': '10',
            'CAPABILITY_ACCOUNT_EXECUTIONS': '3', 'CAPABILITY_GLOBAL_EXECUTIONS': '12',
            'DELIVERY_ACCOUNT_CAPACITY': '2', 'DELIVERY_GLOBAL_CAPACITY': '9',
            'TEMPORAL_TASK_QUEUE': 'fixture-memory', 'WEB_OUTBOX_LEASE_TIMEOUT_SECONDS': '90',
            'WEB_OUTBOX_RECOVERY_INTERVAL_SECONDS': '20',
        }
        services = self.compile({**shared, **worker})
        for name, value in {**shared, **worker}.items():
            with self.subTest(service='hpagent', name=name):
                self.assertEqual(services['hpagent']['environment'].get(name), value)
        for name, value in shared.items():
            self.assertEqual(services['hpagent-api']['environment'].get(name), value)

    def test_api_security_memory_and_sse_environment_reaches_real_settings(self):
        overrides = {
            'WEB_CURSOR_ACTIVE_KEY_ID': 'v2',
            'WEB_CURSOR_KEYS_JSON': json.dumps({'v2': 'fixture-key-' * 4}),
            'HINDSIGHT_URL': 'http://memory-fixture.invalid:8888',
            'HINDSIGHT_API_KEY': 'fixture-memory-key', 'LOG_LEVEL': 'DEBUG',
            'WEB_SSE_HANDSHAKE_BUFFER_EVENTS': '99', 'WEB_SSE_HANDSHAKE_BUFFER_BYTES': '12345',
            'WEB_SSE_KEEPALIVE_SECONDS': '17.5', 'WEB_SSE_MAX_CONNECTIONS': '11',
            'WEB_TERMINAL_PUBLISHER_POLL_SECONDS': '0.75',
        }
        services = self.compile(overrides)
        for name, value in overrides.items():
            self.assertEqual(services['hpagent-api']['environment'].get(name), value, name)
        for name in ('HINDSIGHT_URL', 'HINDSIGHT_API_KEY'):
            self.assertEqual(services['hpagent']['environment'].get(name), overrides[name])
        import sys
        sys.path.insert(0, str(ROOT / 'src'))
        try:
            from web_api.config import WebApiSettings
            # Compose represents unset optional entries as null; no container env value is set.
            environment = {name: value for name, value in services['hpagent-api']['environment'].items()
                           if value is not None}
            with patch.dict(os.environ, environment, clear=True):
                settings = WebApiSettings.from_env()
        finally:
            sys.path.pop(0)
        self.assertEqual(settings.active_cursor_key_id, 'v2')
        self.assertEqual(set(settings.cursor_signing_keys), {'v2'})
        self.assertEqual(settings.sse_handshake_buffer_events, 99)
        self.assertEqual(settings.sse_handshake_buffer_bytes, 12345)
        self.assertEqual(settings.sse_keepalive_seconds, 17.5)
        self.assertEqual(settings.sse_max_connections, 11)
        self.assertEqual(settings.terminal_publisher_poll_seconds, 0.75)
        self.assertEqual(settings.hindsight_url, overrides['HINDSIGHT_URL'])
        self.assertEqual(settings.hindsight_api_key, overrides['HINDSIGHT_API_KEY'])

    def test_absent_optional_parameters_keep_code_defaults_and_existing_presets(self):
        services = self.compile({'ARTIFACT_HTML_MAX_BYTES': '12345'})
        worker = services['hpagent']['environment']
        api = services['hpagent-api']['environment']
        self.assertIsNone(worker.get('WORK_GLOBAL_SUBAGENTS'))
        self.assertIsNone(worker.get('ARTIFACT_HTML_MAX_BYTES'))
        self.assertIsNone(api.get('WEB_SSE_KEEPALIVE_SECONDS'))
        self.assertEqual(worker['WORK_ACCOUNT_COORDINATORS'], '3')
        self.assertEqual(worker['WORK_GLOBAL_COORDINATORS'], '16')
        self.assertEqual(worker['CAPACITY_MODEL_GLOBAL'], '8')
        self.assertEqual(api['FILE_MAX_BYTES'], '33554432')
        self.assertEqual(api['FILE_MAX_COUNT_PER_MESSAGE'], '5')
        self.assertEqual(api['RUN_BUDGET_MODE'], 'enforce')
        self.assertIsNone(api.get('FILE_DIRECT_READ_MAX_BYTES'))
        self.assertIsNone(api.get('FILE_CLEANUP_INTERVAL_SECONDS'))
        self.assertIsNone(services['hpagent-document-worker']['environment'].get('FILE_MAX_BYTES'))


if __name__ == '__main__':
    unittest.main()
