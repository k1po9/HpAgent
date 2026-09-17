# Testing

## Python

```bash
make test-existing          # non-PostgreSQL suite
make lint typecheck
PYTHONPATH=src python3 -m pytest path/to/test.py
```

For PostgreSQL-backed suites:

```bash
make db-up
make migrate
make test-db
make test-api
```

Tests marked `temporal` require a reachable Temporal server. Reliability and worker-recovery tests may create disposable processes or containers; read the selected test before running it against a shared environment.

## Web

```bash
make web-install
make ci-web              # lint + typecheck + build + unit tests
make e2e                 # Playwright
```

## Focused capability checks

```bash
python scripts/check/mcp-health.py --help
python scripts/check/models.py --help
./scripts/check/gateway-smoke.sh
./scripts/check/release-smoke.sh
```

The release smoke requires configured credentials and a functioning model provider. The model checks call external providers unless their help/dry paths are used.

## Benchmarks

Benchmark runners live in `scripts/benchmarks/`. The agent-strategy runner is inert until its explicit execution mode is selected:

```bash
make agent-benchmark-check
make agent-benchmark-run
```

Do not treat benchmark scripts as routine unit tests; they may require live infrastructure, model credentials, and significant time.
