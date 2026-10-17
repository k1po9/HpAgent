.PHONY: install lint typecheck test test-existing test-db test-api migrate db-up db-down ci ci-web web-install web-lint web-typecheck web-build web-test web-dev e2e agent-benchmark-check agent-benchmark-run

NPM ?= npm

PYTHON ?= python3
export MIGRATION_DATABASE_URL APP_DATABASE_URL WORKER_DATABASE_URL

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

test-db: check-test-env
	@PYTHONPATH=src $(PYTHON) -m pytest -m postgres test/web_persistence

test-api: check-test-env
	@PYTHONPATH=src $(PYTHON) -m pytest -m postgres test/web_api

migrate:
	@test -n "$$MIGRATION_DATABASE_URL" || { echo "必须显式设置 MIGRATION_DATABASE_URL；目标库会执行迁移" >&2; exit 2; }
	@PYTHONPATH=src APP_DATABASE_URL="$$MIGRATION_DATABASE_URL" $(PYTHON) -m persistence.migrate

db-up:
	docker compose up -d app-postgres

db-down:
	docker compose stop app-postgres

ci: lint typecheck test-existing test-db test-api

# Frontend quality gate; browser E2E runs separately via `make e2e`.
ci-web: web-lint web-typecheck web-build web-test

web-install:
	cd web && $(NPM) ci

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

.PHONY: help docs-check e2e-contract e2e-canonical scripts-test
help:
	@echo "开发：install web-install web-dev；静态：lint typecheck docs-check；前端：ci-web"
	@echo "浏览器：e2e-contract（构建产物+夹具）；e2e/e2e-canonical（显式隔离DB/Redis/File Store）"
	@echo "脚本副作用、环境参数及运维入口见 scripts/README.md；测试边界见 docs/development/testing.md"

docs-check:
	$(PYTHON) scripts/check/docs-links.py

e2e-contract:
	cd web && $(NPM) run test:e2e:contract

e2e-canonical:
	cd web && $(NPM) run test:e2e:canonical

scripts-test:
	$(PYTHON) -m unittest discover -s test/scripts -v

.PHONY: check-test-env
check-test-env:
	@test -n "$$MIGRATION_DATABASE_URL" -a -n "$$APP_DATABASE_URL" -a -n "$$WORKER_DATABASE_URL" || { echo "数据库测试必须显式设置三个专用同库DSN" >&2; exit 2; }
