.PHONY: install lint typecheck test test-existing test-db test-api migrate db-up db-down ci ci-web web-install web-lint web-typecheck web-build web-test web-dev e2e agent-benchmark-check agent-benchmark-run

NPM ?= npm

MIGRATION_DATABASE_URL ?= postgresql://hpagent_migrate:hpagent_migrate@localhost:5434/hpagent
APP_DATABASE_URL ?= postgresql://hpagent_api:hpagent_api@localhost:5434/hpagent
WORKER_DATABASE_URL ?= postgresql://hpagent_worker:hpagent_worker@localhost:5434/hpagent
PYTHON ?= python3

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check src/web_domain src/persistence src/web_api test/web_persistence test/web_api

typecheck:
	$(PYTHON) -m mypy

test:
	PYTHONPATH=src $(PYTHON) -m pytest

test-existing:
	PYTHONPATH=src $(PYTHON) -m pytest -m "not postgres" test

test-db:
	PYTHONPATH=src MIGRATION_DATABASE_URL=$(MIGRATION_DATABASE_URL) APP_DATABASE_URL=$(APP_DATABASE_URL) WORKER_DATABASE_URL=$(WORKER_DATABASE_URL) $(PYTHON) -m pytest -m postgres test/web_persistence

test-api:
	PYTHONPATH=src MIGRATION_DATABASE_URL=$(MIGRATION_DATABASE_URL) APP_DATABASE_URL=$(APP_DATABASE_URL) WORKER_DATABASE_URL=$(WORKER_DATABASE_URL) $(PYTHON) -m pytest -m postgres test/web_api

migrate:
	PYTHONPATH=src APP_DATABASE_URL=$(MIGRATION_DATABASE_URL) $(PYTHON) -m persistence.migrate

db-up:
	docker compose up -d app-postgres

db-down:
	docker compose stop app-postgres

ci: lint typecheck test-existing test-db test-api

# Phase E frontend gate (phase-e-report.md §8); E2E runs via `make e2e`.
ci-web: web-lint web-typecheck web-build web-test

web-install:
	cd web && $(NPM) install

web-lint:
	cd web && $(NPM) run lint

web-typecheck:
	cd web && $(NPM) run typecheck

web-build:
	cd web && $(NPM) run build

web-test:
	cd web && $(NPM) test

web-dev:
	cd web && $(NPM) run dev

e2e:
	cd web && $(NPM) run test:e2e

agent-benchmark-check:
	bash scripts/benchmarks/agent_strategy/agent_strategy.sh check

agent-benchmark-run:
	bash scripts/benchmarks/agent_strategy/agent_strategy.sh run
