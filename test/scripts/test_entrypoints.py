"""Hermetic tests: no Docker daemon, credentials, network or application database."""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


smoke = load("release_smoke", "scripts/check/release_smoke.py")
links = load("docs_links", "scripts/check/docs-links.py")


class ReleaseSmokeTests(unittest.TestCase):
    def test_help_and_rejected_arguments_do_not_execute(self):
        with patch.object(smoke.subprocess, "run") as run:
            for argv, status in [(["--help"], 0), ([], 2), (["--execute", "--timeout", "nan"], 2)]:
                with self.subTest(argv=argv), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        smoke.main(argv)
                    self.assertEqual(result.exception.code, status)
            run.assert_not_called()

    def test_gateway_uses_configured_production_origin_and_rejects_mismatch(self):
        services = {"hpagent-api": {"environment": {"WEB_PUBLIC_ORIGIN": "https://example.invalid"}}}
        self.assertEqual(smoke.resolve_gateway(None, services), "https://example.invalid")
        with self.assertRaises(smoke.SmokeError):
            smoke.resolve_gateway("http://127.0.0.1", services)

    def test_topology_requires_worker_gateway_and_real_executor(self):
        services = {name: {} for name in smoke.SERVICES + ("hpagent-migrate",)}
        for name in ("hpagent-api", "hpagent"):
            services[name]["environment"] = {"HPAGENT_ENV": "production"}
            services[name]["healthcheck"] = {"test": ["CMD", "fixture-health"]}
        services["web-gateway"]["ports"] = [{"target": 80}]
        self.assertEqual(smoke.validate_topology({"services": services}), services)
        services["hpagent-api"]["environment"]["WEB_FAKE_EXECUTOR_ENABLED"] = "true"
        with self.assertRaises(smoke.SmokeError):
            smoke.validate_topology({"services": services})
        services.pop("hpagent-document-worker")
        with self.assertRaises(smoke.SmokeError):
            smoke.validate_topology({"services": services})

    def test_exercise_accepts_current_contract_and_rejects_unattributed_history(self):
        class Fake:
            def __init__(self, attributed=True):
                self.calls = []
                self.attributed = attributed

            def request(self, method, path, payload=None, csrf=None, expected=(200,)):
                self.calls.append((method, path, payload, csrf, expected))
                if path == "/api/v1/me":
                    return {"csrf_token": "fixture", "account": {"account_id": "fixture"}}
                if path == "/api/v1/conversations":
                    return {"conversation": {"conversation_id": "c"}}
                if method == "POST" and path.endswith("/messages"):
                    return {"run": {"run_id": "r"}}
                if path.startswith("/api/v1/runs/"):
                    return {"run": {"status": "succeeded"}}
                if "?limit=" in path:
                    return {"items": [{"role": "assistant", "status": "completed", "produced_by_run_id": "r" if self.attributed else "old", "content": "fixture answer"}]}
                return {}

        client = Fake()
        smoke.exercise(client, 'user"quoted', 'password"quoted', 1)
        self.assertEqual(client.calls[0][4], (303,))
        self.assertEqual(next(c for c in client.calls if c[0] == "POST" and c[1].endswith("/messages"))[4], (202,))
        with self.assertRaises(smoke.SmokeError):
            smoke.exercise(Fake(False), "user", "password", 1)

    def test_http_client_sends_origin_and_valid_json_handles_303_without_redirect(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        seen = []

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                seen.append((self.headers, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
                self.send_response(303)
                self.send_header("Location", "/must-not-follow")
                self.send_header("Set-Cookie", "fixture=valid; Path=/; HttpOnly")
                self.end_headers()

            def log_message(self, *_):
                pass

        with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f"http://127.0.0.1:{server.server_port}"
            try:
                smoke.Client(origin, 1).request("POST", "/auth/login", {"password": 'quoted"value'}, expected=(303,))
            finally:
                server.shutdown()
                thread.join()
        self.assertEqual(seen[0][0]['Origin'], origin)
        self.assertEqual(seen[0][1]['password'], 'quoted"value')

    def test_wait_services_never_skips_missing_worker(self):
        from types import SimpleNamespace
        args = SimpleNamespace(compose_file='fixture.yaml', profile='web-prod', timeout=1)
        with patch.object(smoke, 'output', return_value=''), patch.object(smoke.time, 'monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(smoke.SmokeError, 'hpagent'):
                smoke.wait_services(args, {name: {} for name in smoke.SERVICES})


class SafeShellTests(unittest.TestCase):
    def test_help_does_not_call_docker_and_invalid_backup_is_rejected(self):
        scripts = ['check/release-smoke.sh', 'check/schema-runtime-smoke.sh', 'check/gateway-smoke.sh', 'operations/backup.sh', 'operations/docker-offline-export.sh', 'operations/docker-offline-load.sh', 'dev/mcp.sh', 'dev/reset.sh', 'benchmarks/agent_strategy/agent_strategy.sh']
        with tempfile.TemporaryDirectory() as temp:
            sentinel = Path(temp) / 'CALLED'
            docker = Path(temp) / 'docker'
            docker.write_text(f'#!/bin/sh\ntouch "{sentinel}"\nexit 99\n')
            docker.chmod(0o700)
            environment = {**os.environ, 'PATH': temp + os.pathsep + os.environ['PATH']}
            for script in scripts:
                result = subprocess.run(['bash', str(ROOT / 'scripts' / script), '--help'], cwd=temp, env=environment, capture_output=True)
                self.assertEqual(result.returncode, 0, (script, result.stderr))
            alias = Path(temp) / 'repository-alias'
            alias.symlink_to(ROOT, target_is_directory=True)
            for location in [str(ROOT / 'backups'), str(ROOT / '.data'), str(alias / 'backups'), '/', 'relative']:
                result = subprocess.run(['bash', str(ROOT / 'scripts/operations/backup.sh'), '--output', location], cwd=temp, env=environment, capture_output=True)
                self.assertEqual(result.returncode, 2)
            self.assertFalse(sentinel.exists())


class DocumentationTests(unittest.TestCase):
    def test_links_images_references_and_fences(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'space name.md').write_text('ok')
            p = root / 'README.md'
            p.write_text('[ok](space%20name.md#anchor)\n![missing](absent.png)\n[ref]: <space name.md>\n```md\n[ignore](not-real)\n```\n')
            errors = links.check([p], root=root, check_ignored=False)
            self.assertEqual(len(errors), 1)
            self.assertEqual(errors[0][-1], 'absent.png')


if __name__ == '__main__':
    unittest.main()
