# Transactional Outbox Recovery Benchmark Report

## Environment

- Git commit: `3c192760c30dae6a9125926b46c7a3c3d993ccb5`
- Branch: `feat/hpagent-web`
- Timestamp: `2026-08-18T14:52:56.799863+00:00`
- Python: `3.12.3`
- Temporal Python SDK: `1.31.0`
- Docker Compose: `5.3.1`
- Host: `Linux-6.18.33.2-microsoft-standard-WSL2-x86_64-with-glibc2.39`
- Temporal namespace: `hpagent-outbox-benchmark`
- PostgreSQL database: `hpagent_outbox_benchmark`

## Method

The Web API accepted all requests while no Outbox dispatcher or Temporal Web Worker was running
in the isolated benchmark namespace. Each HTTP request used the real API middleware, command
service, and PostgreSQL transaction. The runner verified queued Runs and pending `start_run`
events before starting a real replacement Worker subprocess. That process used the production
Outbox claim/ack code, deterministic Workflow IDs, real Temporal workflows, and benchmark-only
Activities that complete Runs without an external model provider.

## Results

- Accepted requests: **30**
- Persisted requests: **30**
- Eventually processed Runs: **30**
- Temporal completed Workflows: **30**
- Terminal completed Runs: **30**
- Lost Runs: **0**
- Duplicate Workflows: **0**
- Outbox recovery success rate: **100.00%**
- Backlog drain time: **11916.932 ms**
- Eventual completion latency P50: **9718.245 ms**
- Eventual completion latency P95: **12664.568 ms**

## Failure Analysis

Observed failure count: **0**. Failure details are retained in the JSON
summary and per-request CSV rows.

## Limitations

HTTP was exercised through Starlette's in-process `TestClient`, not an external network socket.
Authentication used a benchmark credential adapter. All Web API routing, CSRF/idempotency checks,
PostgreSQL transactions, Outbox consumption, deterministic Workflow start, Temporal execution,
and Run lifecycle commits were real. The Agent Activity was intentionally fake, so this experiment
measures Outbox/orchestration recovery rather than model-provider reliability. Dispatcher
claim-during-crash injection (experiment B) remains optional and is not included in these numbers.
Post-completion `retain_memory` and `publish_terminal_event` consumers were intentionally not
started; their newly emitted events remain pending and are outside the measured `start_run` backlog.

## Reproduction

```bash
docker compose up -d app-postgres temporal-postgres temporal
PYTHONPATH=src TEMPORAL_HOST=localhost:7233 \
  TEMPORAL_NAMESPACE=hpagent-outbox-benchmark \
  .venv/bin/python scripts/benchmarks/outbox_recovery_benchmark.py --requests 30
```
