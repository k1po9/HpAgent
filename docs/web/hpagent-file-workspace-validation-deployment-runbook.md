# HpAgent File Workspace 验证与部署操作流程

- 适用分支：`feat/hpagent-web`
- 当前实现提交：`d94f2ed` 起，包含当前分支的附件 Composer 阶段
- 基线文档：`docs/web/hpagent-file-workspace-agent-p0-p1-guide.md`

> **重要安全约束**：不要把当前运行中的 `hpagent` 数据库直接用于 pytest。
> PostgreSQL 集成测试 fixture 会执行 `TRUNCATE ... CASCADE`。必须创建独立测试库。

> **环境边界**：本文中的 Docker Compose 是本地测试环境；开发环境位于云服务器，
> 不在本流程的连接、Migration、重启或回滚范围内。

## 1. 当前阶段

已完成：

- 文件表、绑定表、Run scope 和 RunBudget migrations。
- 安全上传、下载、逻辑删除与 orphan TTL 清理。
- Run input/scratch/output 隔离及只读结构化分析工具。
- Durable 工具和模型调用预算记账，包括 provider fallback。
- Trace metadata allowlist、脱敏、大小限制及文件聚合节点。
- 文件能力 Feature Flag、目录重叠和生产配置失败关闭。
- 本地 Docker 测试环境的独立 File Store / Run Root 挂载。
- 前端附件 Composer、上传状态、失败阻断、移除清理和历史消息附件展示。
- 浏览器侧上传协议、`file_ids` 消息绑定和 PostgreSQL 原子绑定测试。

尚未完成：

- `transform_file`、`publish_output` Durable 写工具。
- Legacy Web 单 Activity 的完整预算接线。
- BudgetCheck Trace、文件/预算指标。
- 输出文件卡片与 `publish_output` 写链路。
- 云端开发环境部署及灰度（本地测试环境验收完成前不得执行）。

当前推进图：

```text
[P0/P1 后端与迁移] ✅
          ↓
[本地 Compose 隔离挂载] ✅
          ↓
[真实 HTTP 上传/下载/删除] ✅
          ↓
[前端附件选择/上传/绑定] ✅
          ↓
[单元 + PostgreSQL + UI 验收] ✅
          ↓
[本地人工浏览器验收] ⏳
          ↓
[云端开发环境灰度] ⛔ 未授权、未执行
```

## 2. 前置检查

在仓库根目录执行：

```bash
cd /home/hp/workspace/HpAgent_web
git status --short --branch
docker compose ps
docker compose exec -T app-postgres pg_isready
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
```

预期：

- PostgreSQL 输出 `accepting connections`。
- API 两个健康检查均返回 2xx。
- 确认当前分支领先远端的提交尚未丢失。

## 3. 备份本地测试服务数据库

先创建不进入 Git 的备份目录：

```bash
mkdir -p .data/backups
docker compose exec -T app-postgres \
  pg_dump -U hpagent_migrate -d hpagent -Fc \
  > ".data/backups/hpagent-before-file-workspace-$(date +%Y%m%d-%H%M%S).dump"
```

确认备份不是空文件：

```bash
ls -lh .data/backups/
```

不要在未验证备份的情况下执行生产 migration 或重建数据库。

## 4. 创建隔离测试数据库

以下操作只创建 `hpagent_test`，不会修改当前 `hpagent` 数据库：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname='hpagent_test'" \
  | grep -q 1 \
  || docker compose exec -T app-postgres \
    createdb -U hpagent_migrate hpagent_test
```

确认两个数据库同时存在：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d postgres \
  -c "SELECT datname FROM pg_database WHERE datname IN ('hpagent', 'hpagent_test') ORDER BY datname;"
```

## 5. 为宿主测试进程设置数据库 URL

从当前 `docker-compose.yaml` 中复制三个角色的密码，但不要把密码写入本文档、
shell history、测试日志或 Git。宿主通过映射端口 `127.0.0.1:5434` 访问测试库：

```bash
export MIGRATION_DATABASE_URL='postgresql://hpagent_migrate:<迁移密码>@127.0.0.1:5434/hpagent_test'
export APP_DATABASE_URL='postgresql://hpagent_api:<API密码>@127.0.0.1:5434/hpagent_test'
export WORKER_DATABASE_URL='postgresql://hpagent_worker:<Worker密码>@127.0.0.1:5434/hpagent_test'
```

只检查变量是否存在，不打印密码：

```bash
for name in MIGRATION_DATABASE_URL APP_DATABASE_URL WORKER_DATABASE_URL; do
  if [ -n "${!name:-}" ]; then echo "$name=set"; else echo "$name=missing"; fi
done
```

测试完成后清除：

```bash
unset MIGRATION_DATABASE_URL APP_DATABASE_URL WORKER_DATABASE_URL
```

## 6. 在隔离库执行 migration

```bash
PYTHONPATH=.:src .venv/bin/python -c '
import os
from persistence.migrate import migrate
migrate(os.environ["MIGRATION_DATABASE_URL"])
'
```

确认 `017`、`018`、`019`、`020` 已应用：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent_test \
  -c "SELECT version, applied_at FROM hpagent.schema_migrations ORDER BY version;"
```

## 7. 执行测试

### 7.1 先运行文件与预算定向测试

```bash
PYTHONPATH=.:src .venv/bin/pytest -q \
  test/test_file_capability_config.py \
  test/test_file_analysis_tools.py \
  test/test_file_cleanup.py \
  test/test_file_upload_contract.py \
  test/test_file_workspace_migration_contract.py \
  test/test_file_workspace_security.py \
  test/test_run_file_workspace.py \
  test/test_tenant_file_store.py \
  test/test_run_budget.py \
  test/test_trace_events.py
```

### 7.2 运行新增 PostgreSQL 集成测试

```bash
PYTHONPATH=.:src .venv/bin/pytest -q \
  test/web_persistence/test_run_budget.py \
  test/web_persistence/test_agent_trace.py \
  test/web_persistence/test_file_cleanup.py
```

这些测试不得显示 `SKIPPED ... database URLs are required`。

### 7.3 运行全部 PostgreSQL 测试

```bash
PYTHONPATH=.:src .venv/bin/pytest -q -m postgres
```

### 7.4 静态检查

```bash
git diff --check
.venv/bin/ruff check src test
PYTHONPATH=.:src .venv/bin/mypy src/web_domain src/persistence src/web_api
```

记录失败测试、错误日志和 migration version；不要为了通过测试改用本地服务正在使用的 `hpagent` 库。

### 7.5 前端附件验收

```bash
docker compose exec -T web-dev npm test -- --maxWorkers=1
docker compose exec -T web-dev npm run lint
docker compose exec -T web-dev npm run build
```

测试容器资源不足时必须保持 `--maxWorkers=1`；并发 worker 启动超时不等于断言失败。
浏览器人工验收至少覆盖：附件按钮仅在 `file_upload=true` 时出现、上传中禁止发送、
上传失败可移除、发送请求包含 `file_ids`、历史消息显示可下载附件。

## 8. Compose 文件能力配置

本地测试 Compose 已应用以下配置。若迁移到云端开发环境，仍需人工合并环境变量和挂载，
不要直接覆盖服务器上的 Compose 文件。

推荐使用三个互不重叠的根：

```text
Git Workspace: /app/.data/workspace
File Store:    /var/lib/hpagent/file-store
Run Root:      /var/lib/hpagent/file-runs
```

建议新增两个 named volumes：

```yaml
volumes:
  hpagent-file-store:
  hpagent-file-runs:
```

`hpagent-api` 只挂载 File Store，不挂载 Git Workspace 或 Run Root：

```yaml
services:
  hpagent-api:
    environment:
      WEB_FILE_UPLOAD_ENABLED: "true"
      WEB_FILE_TRANSFORM_ENABLED: "false"
      WEB_FILE_SHELL_ENABLED: "false"
      FILE_STORE_ROOT: /var/lib/hpagent/file-store
      FILE_MAX_BYTES: "134217728"
      RUN_BUDGET_MODE: enforce
    volumes:
      - hpagent-file-store:/var/lib/hpagent/file-store
```

`hpagent` Worker 挂载 File Store 和独立 Run Root：

```yaml
services:
  hpagent:
    environment:
      DURABLE_AGENT_ENABLED: "true"
      WEB_FILE_UPLOAD_ENABLED: "true"
      WEB_FILE_TRANSFORM_ENABLED: "false"
      WEB_FILE_SHELL_ENABLED: "false"
      FILE_STORE_ROOT: /var/lib/hpagent/file-store
      FILE_RUN_ROOT: /var/lib/hpagent/file-runs
      FILE_MAX_BYTES: "134217728"
      FILE_CLEANUP_INTERVAL_SECONDS: "300"
      RUN_BUDGET_MODE: enforce
    volumes:
      - hpagent-file-store:/var/lib/hpagent/file-store
      - hpagent-file-runs:/var/lib/hpagent/file-runs
```

在 `transform_file` / `publish_output` 和完整 E2E 完成前保持：

```text
WEB_FILE_TRANSFORM_ENABLED=false
WEB_FILE_SHELL_ENABLED=false
```

渲染检查会包含密码，不要提交输出文件：

```bash
docker compose config > /tmp/hpagent-compose-rendered.yaml
docker compose config --services
```

检查点：

- API 没有 Git Workspace 挂载。
- API 没有 Run Root 挂载。
- File Store、Run Root、Git Workspace 两两不重叠。
- 生产环境使用绝对路径和 `RUN_BUDGET_MODE=enforce`。
- Host Bash 保持关闭。

## 9. 将 migration 应用于本地测试服务数据库

仅在隔离库测试全部通过并确认备份可用后执行：

```bash
docker compose --profile web run --rm hpagent-migrate
```

确认本地测试服务库 migration：

```bash
docker compose exec -T app-postgres \
  psql -U hpagent_migrate -d hpagent \
  -c "SELECT version, applied_at FROM hpagent.schema_migrations ORDER BY version;"
```

## 10. 重启并验证

源码已通过 bind mount 挂载，纯 Python/SQL 变更通常不需要 rebuild：

```bash
docker compose restart hpagent-api hpagent
docker compose ps
curl --fail http://127.0.0.1:8080/health/live
curl --fail http://127.0.0.1:8080/health/ready
```

观察启动失败关闭和清理日志：

```bash
docker compose logs --since=10m hpagent-api hpagent \
  | grep -E 'file capability|Run file workspace|File cleanup|ERROR|Traceback'
```

如果配置错误，Worker 应拒绝启动，而不是回退到共享 Git Workspace。

## 11. 灰度顺序

1. `RUN_BUDGET_MODE=enforce`，但文件 Feature Flag 保持关闭。
2. 打开 `WEB_FILE_UPLOAD_ENABLED`，只验证上传、绑定、只读分析和下载。
3. 验证 Run 完成、失败、取消后 Run Root 均被清理。
4. 验证未绑定 ready 文件到期后被 orphan cleanup 删除。
5. 检查 Trace 中不存在文件正文、query、路径、prompt 或原始文件名。
6. 在写工具和前端完成前，不打开 transform/shell。

## 12. 回滚

应用代码异常时，先关闭能力而不是删除数据：

```text
WEB_FILE_UPLOAD_ENABLED=false
WEB_FILE_TRANSFORM_ENABLED=false
WEB_FILE_SHELL_ENABLED=false
```

然后重启：

```bash
docker compose restart hpagent-api hpagent
```

注意：

- `017`—`020` 是前向 migration，不建议在故障期间手工删除表、索引或约束。
- 不删除 `hpagent-file-store` volume；关闭 Feature Flag 后保留对象供排查。
- 不运行 `docker compose down -v`，它会删除数据库和持久化卷。
- 如需数据库恢复，先停止写入，再使用第 3 节的 dump；不要边运行服务边覆盖本地测试服务库。

## 13. 清理隔离测试库（可选）

确认不再需要保留集成测试数据后，可删除隔离测试库。该操作不可恢复，执行前再次确认目标名称只能是 `hpagent_test`：

```bash
docker compose exec -T app-postgres \
  dropdb -U hpagent_migrate --if-exists hpagent_test
unset MIGRATION_DATABASE_URL APP_DATABASE_URL WORKER_DATABASE_URL
```

该命令只能用于 `hpagent_test`，不得把目标替换成 `hpagent`。
