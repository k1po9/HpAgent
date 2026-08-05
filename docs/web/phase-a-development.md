# Phase A 开发说明

Phase A 采用 PostgreSQL 16、`psycopg` 3（同步、显式 SQL Repository/UoW 边界）和 `uuid6.uuid7`。业务表位于独立 `hpagent` schema；它与 Temporal 和 Hindsight 的 PostgreSQL 服务没有共享账号、schema 或 volume。

## 本地命令

```bash
make install
make db-up
make migrate
make ci
```

迁移默认使用 `MIGRATION_DATABASE_URL=postgresql://hpagent_migrate:hpagent_migrate@localhost:5434/hpagent`。业务命令和 Worker 分别使用 `APP_DATABASE_URL` 与 `WORKER_DATABASE_URL`；运行账号仅获得各自的表级权限，且不能访问 `schema_migrations`。生产环境必须替换 compose 中的示例密码，并通过 secret manager 注入连接串。

`make test-db` 是真实 PostgreSQL 合约测试入口；它不会用 SQLite 降级。GitHub Actions 使用同一 migration runner 和 pytest marker，并上传 JUnit 报告。

## 回滚

初始 migration 是绿色建库。发布前发生问题时，停止 Web API/Worker 并删除仅该环境的 `hpagent` schema 或整个独立 app-postgres 数据卷；不得影响 Temporal/Hindsight 数据库。已有生产数据后的后续 migration 必须提供独立的 expand/contract 回滚说明，不能修改此初始 migration。
