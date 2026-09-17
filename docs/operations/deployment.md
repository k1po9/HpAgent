# Deployment

## Compose topology

Core infrastructure services are `app-postgres`, `redis`, `temporal-postgres`, `temporal`, `hindsight-postgres`, `hindsight`, `searxng`, and `gotenberg`.

Application services are:

- `hpagent-migrate` — one-shot application schema migration.
- `hpagent-api` — FastAPI admission, query, auth, file, artifact, research, and SSE surface.
- `hpagent` — main orchestration worker and enabled QQ ingress.
- `hpagent-document-worker` — dedicated heavy-document activity worker.
- `web-dev` — Vite development frontend.
- `web-gateway` — production Nginx frontend/API gateway.

Profiles:

- `web`: API, workers, migrations, dependencies, and Vite.
- `web-prod`: API, workers, migrations, dependencies, and production gateway.
- `agent`: workers, migrations, and dependencies without the Web API/frontend.
- `qq`: optional NapCat service and dependencies.
- `tools`: optional Temporal UI and dependencies.

## Start

```bash
cp .env.example .env
docker compose --profile web-prod up -d --build
docker compose --profile web-prod ps
curl --fail http://127.0.0.1:8080/health/ready
curl --fail http://127.0.0.1:${WEB_GATEWAY_PORT:-80}/
```

Set production secrets, provider credentials, `WEB_PUBLIC_ORIGIN`, and `WEB_COOKIE_SECURE=true` as appropriate before exposure. Compose binds PostgreSQL, Redis, Temporal, SearXNG, Gotenberg, Hindsight, and the API to loopback; the gateway is the intended public edge.

For offline image transfer, use `scripts/operations/docker-offline-export.sh` and `scripts/operations/docker-offline-load.sh`.
