"""Model checker CLI regression tests; every provider call is replaced by a fixture."""
import asyncio
import contextlib
import importlib.util
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("model_cli", ROOT / "scripts/check/models.py")
models = importlib.util.module_from_spec(spec)
spec.loader.exec_module(models)


class ModelCheckerTests(unittest.TestCase):
    def test_invalid_parameters_fail_before_loading_credentials_or_requesting_models(self):
        with patch.object(models, "load_env") as load:
            for args in [["-n", "0"], ["--native", "--stream"], ["--categories", "chat,unknown"]]:
                with self.subTest(args=args), patch.object(sys, "argv", ["models.py", *args]), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as result:
                        models.main()
                    self.assertEqual(result.exception.code, 2)
            load.assert_not_called()

    def test_failed_samples_change_exit_code_without_changing_statistics(self):
        fixture = {"chat": [{"provider": "fixture", "model": "fixture"}]}
        for ok in [True, False]:
            response = {"ok": ok, "latency_ms": 1, "error": "fixture failure"}
            with self.subTest(ok=ok), patch.object(models.httpx, "AsyncClient"), patch.object(models, "call_direct", new=AsyncMock(return_value=response)), contextlib.redirect_stdout(io.StringIO()):
                result = asyncio.run(models.run(fixture, 1))
            self.assertEqual(result, 0 if ok else 1)
