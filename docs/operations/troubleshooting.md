# Troubleshooting

## API is not ready

```bash
docker compose --profile web ps
./scripts/operations/logs.sh hpagent-migrate hpagent-api app-postgres --tail 200 --no-follow
```

Check that migration completed successfully, application role passwords match `.env`, and `app-postgres` is healthy.

## Run remains queued

Inspect `hpagent`, `temporal`, and `app-postgres` logs. Confirm the `hpagent-web-lifecycle` and `hpagent-web-agent` workers are running and that the Outbox dispatcher can connect with `WORKER_DATABASE_URL`.

## Model calls fail

Compare `.env` provider variables with `config/models.yaml`. Empty base URLs, keys, or model names make the selected chain unusable. Use `python scripts/check/models.py --help` before running the live connectivity check.

## Memory is unavailable

```bash
curl --fail http://127.0.0.1:8001/health
./scripts/operations/logs.sh hindsight hindsight-postgres --tail 200 --no-follow
```

Verify Hindsight model and SiliconFlow embedding/rerank variables. Memory degradation should not transfer application-state authority away from PostgreSQL.

## Research returns no sources

Check `curl --fail http://127.0.0.1:8085/`, `SEARXNG_URL`, proxy settings, and `searxng` logs. The service reads `config/searxng/settings.yml` through its generated runtime configuration.

## Document normalization is stuck

Inspect `hpagent-document-worker`, `temporal`, `gotenberg`, and PostgreSQL logs. Confirm the `hpagent-document` activity worker is polling and the file-store/document-run volumes are mounted.

## QQ receives no reply

Determine whether the Run completed. If it did not, troubleshoot execution. If it completed, inspect `qq_deliveries`, worker delivery logs, provider credentials, and NapCat/official QQ connectivity. Delivery retries do not execute the Run again.
