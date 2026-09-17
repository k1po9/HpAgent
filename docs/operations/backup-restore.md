# 备份与恢复

## 备份

Compose 服务运行时执行：

```bash
./scripts/operations/backup.sh
BACKUP_DIR=/secure/location ./scripts/operations/backup.sh
```

脚本生成带时间戳的 Application PostgreSQL 与 Hindsight PostgreSQL Custom-format Dump，并压缩 `.data/workspace`。Redis 是临时状态，不进行备份。脚本不包含 File Store Volume 和 Temporal PostgreSQL；如果必须恢复上传/生成的 Blob 或进行中的 Workflow History，需要在部署层额外做 Volume Snapshot。

备份集应与 Repository Revision、`.env`/Secret 引用、镜像版本和 Compose Volume Inventory 一起保存。Dump 应按生产数据保护。

## 恢复

先在隔离环境恢复：

1. 停止 API 和 Worker，避免写入。
2. 使用 Migration Owner 和 `pg_restore` 将应用 Dump 恢复到 `hpagent` 数据库。
3. 将 Hindsight Dump 恢复到 Hindsight 数据库。
4. 将 Workspace Archive 恢复到 `.data/workspace`，保留原有 Ownership 和 Permission。
5. 如果备份中包含 File Store 与 Temporal Volume，从同一恢复点还原。
6. 启动依赖，运行 `hpagent-migrate`，再启动 API/Worker。
7. 验证 `/health/ready`、Worker Poller/Log，并进行一次只读 Account/Conversation 查询后再接收流量。

不要在没有 Reconciliation 方案时，混用不同时间点的 Application PostgreSQL、File Store 或 Temporal Snapshot。
