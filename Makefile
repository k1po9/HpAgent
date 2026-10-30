.PHONY: install lint typecheck test test-existing test-db test-api migrate db-up db-down ci ci-web web-install web-lint web-typecheck web-build web-test web-dev e2e agent-benchmark-check agent-benchmark-run

NPM ?= npm

PYTHON ?= python3
PYTEST_ARGS ?=
export MIGRATION_DATABASE_URL APP_DATABASE_URL WORKER_DATABASE_URL
export PYTHONPATH := $(CURDIR):$(CURDIR)/src:$(CURDIR)/test$(if $(PYTHONPATH),:$(PYTHONPATH))

install:
	$(PYTHON) -m pip install -r requirements-dev.txt

lint:
	$(PYTHON) -m ruff check src/conversation_domain src/web_domain src/persistence src/web_api \
		src/work_domain src/run_domain src/application src/bootstrap src/agent_workflows \
		src/agent_activities src/orchestration src/research_domain src/research_activities \
		src/channels/onebot11.py src/common/channel_result.py src/common/onebot.py src/delivery \
		scripts/check/temporal_registry.py test

typecheck:
	$(PYTHON) -m mypy

test:
	$(PYTHON) -m pytest --require-test-services test $(PYTEST_ARGS)

test-existing: test-unit

test-unit:
	$(PYTHON) -m pytest -m "not postgres and not temporal" test $(PYTEST_ARGS)

test-db:
	$(PYTHON) -m pytest --require-test-services -m "postgres and not temporal" test --ignore=test/web_api $(PYTEST_ARGS)

test-api:
	$(PYTHON) -m pytest --require-test-services -m "postgres and not temporal" test/web_api $(PYTEST_ARGS)

test-temporal:
	$(PYTHON) -m pytest --require-test-services -m temporal test $(PYTEST_ARGS)

migrate:
	@test -n "$$MIGRATION_DATABASE_URL" || { echo "必须显式设置 MIGRATION_DATABASE_URL；目标库会执行迁移" >&2; exit 2; }
	@PYTHONPATH=src APP_DATABASE_URL="$$MIGRATION_DATABASE_URL" $(PYTHON) -m persistence.migrate

db-up:
	docker compose up -d app-postgres

db-down:
	docker compose stop app-postgres

ci: architecture-check lint typecheck test-unit test-db test-api test-temporal

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
	@echo "开发：install web-install web-dev；静态：lint typecheck docs-check architecture-check；前端：ci-web"
	@echo "后端：test-unit（无PG/Temporal）；test-db、test-api、test-temporal（显式专用服务）"
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

.PHONY: test-unit test-temporal

.PHONY: architecture-check
architecture-check:
	$(PYTHON) scripts/check/temporal_registry.py
	$(PYTHON) -m pytest -q test/test_architecture_checks.py test/test_execution_boundaries.py \
		test/test_capability_composition.py test/test_tool_registration_contract.py \
		test/test_capability_contracts.py test/test_durable_agent_contract.py \
		test/test_run_diagnostics_projection.py test/test_agent_tool_policy.py \
		test/test_prompt_catalog.py test/test_model_configuration.py \
		test/test_file_capability_config.py $(PYTEST_ARGS)
