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
API 和 Worker 启动时只读核对迁移清单及校验和。Schema 缺失或不匹配会明确失败；正常启动不会迁移、清库或切换旧 Workspace 路径。开发期旧数据不兼容目标 schema 时，应先停止旧 Worker 并明确处置旧 Workflow，再在**可丢弃的开发环境**中手工重建数据库和重新运行迁移。不要对共享数据执行重建命令。

## 日志与检查

```bash
./scripts/operations/logs.sh --api
./scripts/operations/logs.sh --worker
python scripts/check/mcp-health.py --help
./scripts/check/gateway-smoke.sh
```

`scripts/operations/observability-viewer.py` 可在本机只读查看结构化 JSONL 日志与 PostgreSQL 执行状态；它是排障辅助工具，不是运行时服务。

## 管理员身份预置与恢复

正常 Web 用户绑定 QQ 使用 `POST /api/v1/identity-bindings/qq/challenges` 和 QQ ownership challenge。`scripts/operations/bootstrap-identity.py` 是使用 `MIGRATION_DATABASE_URL` 的管理员预置/恢复工具，仅用于已核实 Web 与 QQ 身份的场景；它直接写入绑定，不验证 QQ 所有权。两端身份均不存在时会建立带 `owner` entitlement 的 Account，但不会创建 Web 登录密码。已分属不同 Account 的绑定会报冲突，不由此工具合并。运行前查看 `python scripts/operations/bootstrap-identity.py --help`。

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
