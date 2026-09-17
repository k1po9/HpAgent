# Operations Runbook

## Start, stop, and restart

```bash
docker compose --profile web up -d --build
docker compose --profile web stop
docker compose --profile web down
docker compose --profile web restart hpagent hpagent-document-worker hpagent-api
```

## Health and status

```bash
docker compose --profile web ps
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
docker compose exec redis redis-cli ping
docker compose exec app-postgres pg_isready -U hpagent_migrate -d hpagent
docker compose exec temporal tctl --address localhost:7233 cluster health
curl --fail http://127.0.0.1:8001/health
```

## Migration

```bash
docker compose --profile web up hpagent-migrate
```

The migration container exits after applying the checksummed SQL history. A nonzero exit blocks API and worker startup.

## Logs and checks

```bash
./scripts/operations/logs.sh --api
./scripts/operations/logs.sh --worker
python scripts/check/mcp-health.py --help
./scripts/check/gateway-smoke.sh
```

## Development reset

`./scripts/dev/reset.sh --help` describes scoped local cleanup. It can terminate local worker processes, terminate matching Temporal workflows, or remove `.data` workspace/log files. For a fully disposable Compose environment, stop it and explicitly remove volumes only when data loss is intended:

```bash
docker compose --profile web down --volumes
```

## Backup

```bash
./scripts/operations/backup.sh
BACKUP_DIR=/secure/location ./scripts/operations/backup.sh
```

See [Backup and Restore](backup-restore.md) before a restore or destructive reset.
