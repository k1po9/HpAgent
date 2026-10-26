"""Operator validation and the service bridge do not call models or expose secrets."""
import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))


def script(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class MemoryConfigCliTests(unittest.TestCase):
    def test_service_only_cannot_claim_an_empty_data_target(self):
        cli = script('memory_cutover', 'scripts/operations/memory-cutover-check.py')
        with patch.object(sys, 'argv', ['memory-cutover', '--service-only', '--require-empty']), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                cli.main()
        self.assertEqual(raised.exception.code, 2)

    def test_validate_is_local_and_bad_values_are_hidden(self):
        cli = script('memory_config', 'scripts/operations/memory-config.py')
        out, err = io.StringIO(), io.StringIO()
        with patch('socket.socket.connect', side_effect=AssertionError('no network')):
            with patch.object(sys, 'argv', ['memory-config', 'validate']), contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(), 0)
        self.assertIn('"status": "valid"', out.getvalue())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bad.yaml'
            path.write_text('recall: {budget: secret-not-for-output}')
            with patch.object(sys, 'argv', ['memory-config', 'validate', '--config', str(path)]), contextlib.redirect_stderr(err):
                self.assertEqual(cli.main(), 2)
            self.assertNotIn('secret-not-for-output', err.getvalue())

    def test_bridge_executes_existing_launcher_with_yaml_environment(self):
        from memory import quality_config

        with patch.dict(sys.modules, {'quality_config': quality_config}):
            bridge = script('memory_entrypoint', 'scripts/operations/hindsight-entrypoint.py')
        env = {'HPAGENT_HINDSIGHT_CONFIG': str(ROOT / 'config/hindsight.yaml'),
               'MINIMAX_API_BASE_URL': 'https://model.invalid/v1', 'MINIMAX_API_KEY': 'fixture-secret',
               'MINIMAX_STANDARD_MODEL': 'memory-model', 'SILICONFLOW_API_KEY': 'vector-secret',
               'SILICONFLOW_BASE_URL': 'https://vector.invalid/v1',
               'SILICONFLOW_EMBEDDING_MODEL': 'vector', 'SILICONFLOW_RERANK_MODEL': 'rank'}
        with patch.dict(os.environ, env, clear=True), patch.object(bridge.os, 'execvpe') as execute:
            bridge.main()
            args = execute.call_args.args
            self.assertEqual(args[:2], ('bash', ['bash', '/app/start-all.sh']))
            self.assertEqual(args[2]['HINDSIGHT_API_LLM_MODEL'], 'memory-model')
            self.assertEqual(args[2]['HINDSIGHT_API_RERANKER_SILICONFLOW_API_KEY'], 'vector-secret')
            self.assertEqual(args[2]['HINDSIGHT_API_TEXT_SEARCH_EXTENSION'], 'native')
            self.assertEqual(args[2]['HINDSIGHT_API_TEXT_SEARCH_EXTENSION_NATIVE_LANGUAGE'], 'english')
        with patch.dict(os.environ, {**env, 'HINDSIGHT_API_TEXT_SEARCH_EXTENSION': 'pgroonga'}, clear=True), patch.object(bridge.os, 'execvpe') as execute, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(bridge.main(), 2)
            execute.assert_not_called()
            self.assertNotIn('fixture-secret', err.getvalue())
