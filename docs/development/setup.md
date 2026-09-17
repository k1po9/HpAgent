# Development Setup

## Dependencies

- Docker Engine and Docker Compose v2 for the supported service topology.
- Python 3.11+ for host-side backend development (CI uses Python 3.12).
- Node.js 20+ and npm for host-side frontend development.

## Environment and models

```bash
cp .env.example .env
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

Fill the provider variables referenced by `config/models.yaml`. Secrets belong in `.env`, never in YAML. Review feature flags and replace development security values before exposing the service.

## Initial startup

```bash
docker compose --profile web up -d --build
docker compose --profile web ps
curl --fail http://127.0.0.1:8080/health/ready
```

The Web profile includes the application and Temporal PostgreSQL instances, Redis, Hindsight, SearXNG, Gotenberg, migration job, API, main worker, document worker, and Vite frontend. Migrations are a dependency of the API and workers. To run them explicitly:

```bash
docker compose --profile web up hpagent-migrate
```

## Host-side frontend

```bash
make web-install
make web-dev
```

Vite listens on <http://127.0.0.1:5173> and proxies API traffic according to `web/vite.config.ts`. The Compose `web-dev` service is the simpler full-stack default.

## Useful optional services

```bash
docker compose --profile tools up -d temporal-web  # http://127.0.0.1:8088
docker compose --profile qq up -d napcat
./scripts/dev/mcp.sh
```

Use `./scripts/operations/logs.sh` while developing and `docker compose --profile web down` to stop the topology.
