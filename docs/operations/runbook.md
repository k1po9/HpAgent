# 运行手册

## 启动、停止与重启

```bash
docker compose --profile web up -d --build
docker compose --profile web stop
docker compose --profile web down
docker compose --profile web restart hpagent hpagent-document-worker hpagent-api
```

## 健康与状态

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

Migration Container 执行带 Checksum 的 SQL 历史后退出；非零退出会阻止 API 和 Worker 启动。

## 日志与检查

```bash
./scripts/operations/logs.sh --api
./scripts/operations/logs.sh --worker
python scripts/check/mcp-health.py --help
./scripts/check/gateway-smoke.sh
```

## 开发环境重置

`./scripts/dev/reset.sh --help` 列出各类本地清理选项。它可以终止本地 Worker、终止匹配的 Temporal Workflow，或删除 `.data` 中的 Workspace/Log。只在确认允许丢失数据时，才对完全可丢弃的 Compose 环境删除 Volume：

```bash
docker compose --profile web down --volumes
```

## 备份

```bash
./scripts/operations/backup.sh
BACKUP_DIR=/secure/location ./scripts/operations/backup.sh
```

执行恢复或破坏性重置前，请先阅读[备份与恢复](backup-restore.md)。
