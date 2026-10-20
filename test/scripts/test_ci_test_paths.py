from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("ci_test_paths", ROOT / "scripts/check/ci-test-paths.py")
assert spec is not None and spec.loader is not None
checks = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checks)


class CiSelectionTests(unittest.TestCase):
    def test_explicit_paths_do_not_prove_all_suites_are_selected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "test").mkdir()
            (root / "test/test_example.py").touch()
            workflow = root / "ci.yml"
            workflow.write_text("run: python -m pytest test/test_example.py\n")
            self.assertEqual(checks.missing_test_paths(workflow, root), [])
            self.assertEqual(set(checks.missing_test_suites(workflow)), checks.REQUIRED_SUITES)

    def test_only_executable_suite_steps_count(self):
        with tempfile.TemporaryDirectory() as directory:
            workflow = Path(directory) / "ci.yml"
            workflow.write_text(
                "# run: make test-temporal\n- name: make test-db\n"
                "  run: make test-unit PYTEST_ARGS=--collect-only\n"
                "- run: make test-api\n"
            )
            self.assertEqual(checks.missing_test_suites(workflow), ["test-db", "test-temporal"])

    def test_repository_ci_covers_every_suite_and_existing_explicit_path(self):
        workflow = ROOT / ".github/workflows/ci.yml"
        self.assertEqual(checks.missing_test_paths(workflow, ROOT), [])
        self.assertEqual(checks.missing_test_suites(workflow), [])
