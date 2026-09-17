# HpAgent

HpAgent is a durable AI assistant platform with Web and QQ entry points backed by one conversation and execution model.

## Core Capabilities

- Web and QQ surfaces share unified `Conversation`, `Message`, `Session`, and `Run` state.
- PostgreSQL transactions and an Outbox admit work before Temporal executes it durably.
- ReAct and Plan-and-Execute strategies use shared Context, Brain, Actions, memory, and workspace capabilities.
- Hindsight provides long-term memory; Redis provides transient coordination and caching.
- Local tools, MCP servers, sandbox execution, files, and account-scoped Git workspaces are available to agent runs.
- Research is a fixed evidence-to-report workflow, separate from agent strategy.
- Artifacts are versioned outputs derived from messages and research.
- Expensive document normalization runs on a dedicated Temporal activity worker.

## Architecture

```text
Web / QQ
   ↓
Application / Conversation
   ↓
PostgreSQL + Outbox
   ↓
Temporal Durable Runtime
   ↓
AgentRunWorkflow
   ↓
ReAct / Plan-and-Execute
   ↓
Context / Brain / Actions / Memory / Workspace
   ↓
Committed Result
   ↓
Web SSE / QQ Delivery
```

See the [architecture documentation](docs/architecture/overview.md) for runtime, state ownership, capability boundaries, reliability, and sequences.

## Quick Start

Prerequisites: Git, Docker Engine, and Docker Compose v2. Python 3.11+ and Node.js 20+ are only needed for host-side development.

1. Prepare configuration:

   ```bash
   cp .env.example .env
   ```

2. Set provider URLs, model names, and API keys in `.env`. The active chains are declared in `config/models.yaml`; at minimum, configure the provider used by its `chat` chain. Replace all `change-me` values before a shared or production deployment.

3. Start the Web development topology. Compose builds the application images, starts PostgreSQL, Redis, Temporal, Hindsight, SearXNG, Gotenberg, the API, both workers, and the Vite frontend:

   ```bash
   docker compose --profile web up -d --build
   ```

4. Migrations run through the one-shot `hpagent-migrate` service before the API and workers start. To apply or re-run them explicitly:

   ```bash
   docker compose --profile web up hpagent-migrate
   ```

5. Verify the backend and open the frontend:

   ```bash
   curl --fail http://127.0.0.1:8080/health/ready
   docker compose --profile web ps
   ```

   Open <http://127.0.0.1:5173>. For the production gateway topology, use `docker compose --profile web-prod up -d --build` and open the configured `WEB_GATEWAY_PORT` (default `80`).

The main worker, API, and frontend can also be targeted independently:

```bash
docker compose --profile web up -d hpagent hpagent-document-worker
docker compose --profile web up -d hpagent-api
docker compose --profile web up -d web-dev
```

## Configuration

Configuration enters through:

- `.env` — credentials, deployment settings, feature flags, and Compose interpolation.
- `config/config.yaml` — runtime defaults for Temporal, Redis, Hindsight, workspace, channels, sandbox, and agent behavior.
- `config/models.yaml` — provider endpoints, credentials by environment-variable reference, model chains, embeddings, reranking, and tool retrieval.
- `config/prompts/` — system identity, guidance, environment, and tool-summary prompts.
- `config/mcp/` — MCP server declarations.
- `config/searxng/` — research search service settings.

See [Configuration Reference](docs/reference/configuration.md).

## Logs

### How to view logs

The canonical entry point is `scripts/operations/logs.sh`:

```bash
# Follow all running services
./scripts/operations/logs.sh

# API or workers only
./scripts/operations/logs.sh --api
./scripts/operations/logs.sh --worker

# Infrastructure, recent output, or named services
./scripts/operations/logs.sh --infra
./scripts/operations/logs.sh --api --tail 100 --no-follow
./scripts/operations/logs.sh temporal app-postgres --tail 200

# Stop the Web topology
docker compose --profile web down
```

Application JSONL files are also written to `.data/logs/hpagent.jsonl` and `.data/logs/web-api.jsonl`. See [Logging](docs/operations/logging.md) for correlation by `run_id`, `conversation_id`, `workflow_id`, and `operation_id`.

## Development

```bash
make install             # Python development dependencies
make test-existing       # unit/non-PostgreSQL suite
make lint typecheck      # Python static checks
make ci-web              # frontend lint, types, build, and unit tests
make web-dev             # host-side Vite development server
make agent-benchmark-check
```

Database-backed suites use `make db-up`, `make migrate`, `make test-db`, and `make test-api`. See [Development Setup](docs/development/setup.md) and [Testing](docs/development/testing.md).

## Repository Layout

```text
src/          Python application, domain, capabilities, workers, and API
web/          React/Vite frontend
config/       Current runtime, model, prompt, MCP, and search configuration
persistence/  PostgreSQL migrations and database initialization
test/         Unit, contract, integration, and fixture code
scripts/      Current development, operations, checks, and benchmarks
docs/         Current architecture, development, operations, and reference docs
artifacts/    Audit and benchmark evidence
```

## Documentation

Start at [docs/README.md](docs/README.md).
