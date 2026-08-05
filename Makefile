.PHONY: install lint typecheck test test-existing test-db test-api migrate db-up db-down ci

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
