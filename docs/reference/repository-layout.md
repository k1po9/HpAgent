# Repository Layout

| Path | Purpose |
| --- | --- |
| `src/` | Python domains, application services, adapters, capabilities, Temporal workflows/activities, workers, and FastAPI. |
| `web/` | React/Vite UI, browser tests, Nginx gateway, and frontend build configuration. |
| `config/` | Current application, model, prompt, MCP, and SearXNG configuration. |
| `persistence/` | Ordered SQL migrations and Compose database initialization. |
| `test/` | Unit, contract, integration, replay, recovery, and test fixture code. |
| `scripts/dev/` | Local development helpers. |
| `scripts/operations/` | Deployment, logging, backup, identity, and observability tools. |
| `scripts/check/` | Current connectivity and deployment checks. |
| `scripts/benchmarks/` | Reproducible strategy, Outbox, and Temporal recovery benchmarks. |
| `docs/` | Current architecture, development, operations, and reference documentation. |
| `artifacts/` | Evidence and benchmark outputs; not the current documentation root. |

Executable schema history in `persistence/migrations/` remains required for fresh installation even when an individual migration is old. Test fixtures belong under `test/fixtures/`; production `config/` contains only runtime configuration.
