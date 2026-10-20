"""Keep infrastructure requirements explicit before any test fixture can run."""
from __future__ import annotations

import os

import pytest

DATABASE_ENV = {"MIGRATION_DATABASE_URL", "APP_DATABASE_URL", "WORKER_DATABASE_URL"}
DATABASE_FIXTURES = {"db", "database_url", "migration_database_url", "worker_database_url", "urls"}


def pytest_addoption(parser):
    parser.addoption(
        "--require-test-services", action="store_true",
        help="Fail before running infrastructure fixtures when service configuration is missing.",
    )


@pytest.hookimpl(trylast=True)
def pytest_collection_modifyitems(config, items):
    required = set()
    unmarked = []
    for item in items:
        postgres = item.get_closest_marker("postgres") is not None
        if DATABASE_FIXTURES.intersection(item.fixturenames) and not postgres:
            unmarked.append(item.nodeid)
        if postgres:
            required.update(DATABASE_ENV)
        if item.get_closest_marker("temporal") is not None:
            required.add("TEMPORAL_HOST")
            if postgres:
                required.update({"TEMPORAL_NAMESPACE", "TEMPORAL_TEST_NAMESPACE"})
        if {"redis_url", "sync_redis"}.intersection(item.fixturenames):
            required.add("REDIS_URL")
    if unmarked:
        raise pytest.UsageError("Database fixtures require @pytest.mark.postgres:\n" + "\n".join(unmarked))
    # Collection is useful for auditing coverage without connecting to services.
    if config.getoption("require_test_services") and not config.option.collectonly:
        missing = sorted(name for name in required if not os.environ.get(name))
        if missing:
            raise pytest.UsageError(
                "Selected tests require explicit isolated service configuration: " + ", ".join(missing)
            )
