#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "${REPO_ROOT}"

cleanup() {
  docker compose up -d --no-deps --force-recreate hpagent hpagent-api >/dev/null
  docker compose stop f4-e2e-model >/dev/null
}
trap cleanup EXIT

docker compose --profile f4-e2e up -d f4-e2e-model
HPAGENT_MODELS_PATH=/app/config/models.f4-e2e.yaml \
WEB_FILE_TRANSFORM_ENABLED=true \
NO_PROXY=localhost,127.0.0.1,::1,f4-e2e-model,searxng,gotenberg,app-postgres,redis,temporal,hindsight,hpagent,hpagent-api \
  docker compose up -d --no-deps --force-recreate hpagent
WEB_PUBLIC_ORIGIN=http://localhost:5173 \
  docker compose up -d --no-deps --force-recreate hpagent-api

for _attempt in $(seq 1 60); do
  if curl --fail --silent http://127.0.0.1:8080/health/ready >/dev/null; then
    break
  fi
  sleep 1
done
sleep 8

cd "${REPO_ROOT}/web"
npx playwright test e2e/f4-approval.spec.ts --config=playwright.live.config.ts
