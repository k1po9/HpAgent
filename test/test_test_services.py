"""Collection guards and public test commands keep execution prerequisites explicit."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize('previous', [None, '/test-existing-imports'])
def test_make_test_entrypoint_exports_source_paths_for_child_workers(tmp_path, previous):
    root = Path(__file__).resolve().parents[1]
    probe = tmp_path / 'python-probe'
    probe.write_text(f'#!{sys.executable}\nimport json, os\nprint(json.dumps(os.environ.get("PYTHONPATH", "").split(os.pathsep)))\n')
    probe.chmod(0o700)
    environment = dict(os.environ)
    environment.pop('PYTHONPATH', None)
    if previous is not None:
        environment['PYTHONPATH'] = previous
    completed = subprocess.run(
        ['make', '--no-print-directory', '-s', 'test-unit', f'PYTHON={probe}'],
        cwd=root, env=environment, check=True, text=True, capture_output=True,
    )
    paths = json.loads(completed.stdout)
    assert paths[:3] == [str(root), str(root / 'src'), str(root / 'test')]
    if previous is not None:
        assert paths[3:] == [previous]


@pytest.fixture
def guarded_suite(pytester, monkeypatch):
    for name in ("MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "WORKER_DATABASE_URL", "TEMPORAL_HOST", "TEMPORAL_NAMESPACE", "TEMPORAL_TEST_NAMESPACE", "REDIS_URL"):
        monkeypatch.delenv(name, raising=False)
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makeini(
        "[pytest]\nasyncio_default_fixture_loop_scope = function\n"
        "markers =\n    postgres\n    temporal\n"
    )
    return pytester


def test_required_database_configuration_fails_before_fixture_execution(guarded_suite):
    guarded_suite.makepyfile('''
        import pytest
        @pytest.fixture
        def db():
            raise AssertionError("fixture must not execute")
        @pytest.mark.postgres
        def test_database(db):
            pass
    ''')
    result = guarded_suite.runpytest("--require-test-services")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*Selected tests require explicit isolated service configuration:*APP_DATABASE_URL*"])


def test_unit_selection_deselects_infrastructure_before_environment_validation(guarded_suite):
    guarded_suite.makepyfile('''
        import pytest
        @pytest.mark.postgres
        def test_database():
            pytest.fail("database test must be deselected")
        @pytest.mark.temporal
        def test_temporal():
            pytest.fail("Temporal test must be deselected")
        def test_unit():
            pass
    ''')
    result = guarded_suite.runpytest("--require-test-services", "-m", "not postgres and not temporal")
    result.assert_outcomes(passed=1, deselected=2)


def test_unmarked_database_fixture_cannot_enter_unit_suite(guarded_suite):
    guarded_suite.makepyfile('''
        import pytest
        @pytest.fixture
        def database_url():
            raise AssertionError("fixture must not execute")
        def test_database(database_url):
            pass
    ''')
    result = guarded_suite.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*Database fixtures require @pytest.mark.postgres:*"])


def test_collection_needs_no_services_but_execution_requires_temporal_and_redis(guarded_suite):
    guarded_suite.makepyfile('''
        import pytest
        @pytest.fixture
        def redis_url():
            pytest.skip("missing Redis must fail before this")
        @pytest.mark.temporal
        def test_stream(redis_url):
            pass
    ''')
    collected = guarded_suite.runpytest("--require-test-services", "--collect-only")
    assert collected.ret == 0
    result = guarded_suite.runpytest("--require-test-services")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*Selected tests require explicit isolated service configuration: REDIS_URL, TEMPORAL_HOST*"])


def test_database_temporal_suite_requires_explicit_namespaces(guarded_suite, monkeypatch):
    for name in ("MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "WORKER_DATABASE_URL", "TEMPORAL_HOST"):
        monkeypatch.setenv(name, "fixture-only")
    guarded_suite.makepyfile('''
        import pytest
        @pytest.mark.postgres
        @pytest.mark.temporal
        def test_execution():
            pytest.fail("missing namespaces must fail before execution")
    ''')
    result = guarded_suite.runpytest("--require-test-services")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*configuration: TEMPORAL_NAMESPACE, TEMPORAL_TEST_NAMESPACE*"])
