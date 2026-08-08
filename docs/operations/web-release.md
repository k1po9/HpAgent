# HpAgent Web —— 发布 / 备份 / 回滚 / 运维 Runbook

> Phase G G-06（`docs/web/phase-g.md` §22-27）。
> 面向生产部署的运维手册，回答：怎么发布、怎么备份、怎么回滚、怎么查 dead-letter、
> 怎么重放 retain_memory、怎么处理 stuck Run、怎么做 Identity 运维。

---

# 1. 部署拓扑速查

```text
Internet ── HTTPS ──► Web Gateway（唯一浏览器入口）
                        │  / + /assets（React 静态 + SPA fallback）
                        │  /api + /auth（反向代理）
                        │  /api/v1/runs/*（SSE，proxy_buffering off）
                        ▼
                   hpagent-api
                        │
              ┌─────────┴─────────┐
              ▼                   ▼
       PostgreSQL(canonical)    Redis(realtime only)
```

QQ / Web Run → 同一个 `hpagent` Worker 进程（`single_process_account_lock`）→
PostgreSQL / Temporal / Hindsight。

**关键事实：**

- **PostgreSQL 是唯一真相源**：Conversation / Message / Run / Session / IdentityBinding /
  Outbox。Redis 只是 transient（SSE/瞬时实时/缓存），允许丢失。
- **Web Gateway 是唯一浏览器入口**。`hpagent-api`、PostgreSQL、Redis、Temporal、
  Hindsight 都不是面向公网的业务入口（compose 已把调试端口绑定到 `127.0.0.1`）。
- **QQ 与 Web 必须共享同一个 Worker 进程**。任何第二个独立 Web Agent Worker 都会
  破坏 workspace 进程锁与 `AccountLockRegistry`，属于无效拓扑。

---

# 2. 发布流程（怎么发布）

## 2.1 发布前检查

```text
[ ] HPAGENT_ENV=production
[ ] WEB_PUBLIC_ORIGIN=https://<真实公网 origin>（必填，拒绝 https://localhost）
[ ] WEB_REAL_AGENT_ENABLED=true
[ ] WEB_REAL_AGENT_GATE_VERSION=c-07-v1
[ ] WEB_UNIFIED_ACCOUNT_ENABLED=true 且 WORKER_DATABASE_URL 已配置
[ ] WEB_FAKE_EXECUTOR_ENABLED=false（生产不允许 true，启动即拒绝）
[ ] WORKSPACE_ISOLATION_MODE=single_process_account_lock 且 Agent Worker replicas=1
[ ] 所有 secret 来自环境/secret file，绝不来自 Git（§5）
[ ] scripts/bootstrap_identity.py 已执行（§8）
```

## 2.2 发布前备份（§24）

```text
1. pg_dump app-postgres
2. pg_dump hindsight-postgres
3. backup .data/workspace
4. record current git SHA
5. record current image/build version
6. run migration
7. deploy new version
8. smoke test
```

## 2.3 执行发布

```bash
# 1) 构建镜像（web-gateway 为 React+Nginx 多阶段构建）
docker compose --profile web build

# 2) 应用前备份（见 §3）
./scripts/backup.sh            # 或手动 pg_dump + workspace 备份

# 3) 启动：app-postgres healthy → hpagent-migrate（一次性）→ api → gateway
docker compose --profile web up -d

# 4) 等待全部 healthy
docker compose ps

# 5) 全链路冒烟（可选，需要真实模型 key + 已 bootstrap 身份）
./scripts/release-smoke.sh

# 6) 网关冒烟（不依赖模型）
./scripts/gateway-smoke.sh
```

启动顺序由 compose `depends_on` 保证：

```text
app-postgres (healthy)
   → hpagent-migrate (service_completed_successfully)
   → hpagent-api (healthy：/health/ready 校验 PostgreSQL)
   → web-gateway (healthy)
```

若 `hpagent-migrate` 失败，`hpagent-api` / `hpagent` 不会启动（§13）。

## 2.4 配置门禁（fail-closed）

以下情况必须拒绝启动，而不是偷偷降级：

| 场景 | 行为 |
|---|---|
| `HPAGENT_ENV=production` + `WEB_FAKE_EXECUTOR_ENABLED=true` | API 启动失败 |
| 生产缺 `WEB_PUBLIC_ORIGIN` / 非 `https://` / 是 `https://localhost` | API 启动失败 |
| `WEB_REAL_AGENT_ENABLED=true` + gate 版本 ≠ `c-07-v1` | Worker 启动失败 |
| `WEB_UNIFIED_ACCOUNT_ENABLED=true` + 缺 `WORKER_DATABASE_URL` | Worker 启动失败，QQ 绝不回退 `accounts.json` |
| `single_process_account_lock` + 第二个独立 Web Worker | 独立入口 fail-closed（`orchestration.web_worker`） |
| 生产 secret 长度 < 32 bytes | API 启动失败 |

---

# 3. 备份（怎么备份）

## 3.1 最小备份集合（§23）

| 数据 | 位置 | 是否必须备份 |
|---|---|---|
| App PostgreSQL（accounts / identity_bindings / conversations / messages / runs / sessions / outbox_events / workflow_executions） | `app-postgres` | **必须** |
| Workspace（用户实际文件 + Git workspace） | `.data/workspace` | **必须** |
| Hindsight PostgreSQL（长期记忆） | `hindsight-postgres` | 正式环境应能备份 |
| Temporal DB（Workflow history） | `temporal-postgres` | 重大升级前记录恢复方式 |
| Redis | — | **不要求**（transient state） |

## 3.2 手工备份命令

```bash
# App PostgreSQL
docker compose exec -T app-postgres \
  pg_dump -U hpagent_migrate -d hpagent -F c -f /tmp/app.dump
docker cp hpagent_web-app-postgres-1:/tmp/app.dump ./backups/app-$(date +%F).dump

# 或宿主机直连（compose 暴露 127.0.0.1:5434）
pg_dump "postgresql://hpagent_migrate:${HPAGENT_MIGRATE_PASSWORD}@127.0.0.1:5434/hpagent" -F c -f backups/app.dump

# Hindsight PostgreSQL
docker compose exec -T hindsight-postgres \
  pg_dump -U hindsight -d hindsight -F c -f /tmp/hindsight.dump
docker cp hpagent_web-hindsight-postgres-1:/tmp/hindsight.dump ./backups/hindsight-$(date +%F).dump

# Workspace（含用户文件与 Git workspace）
tar czf backups/workspace-$(date +%F).tar.gz .data/workspace
```

建议脚本化：`./scripts/backup.sh`（见 §3.3）。

## 3.3 scripts/backup.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
OUT="${BACKUP_DIR:-./backups}"
mkdir -p "$OUT"
stamp="$(date +%F-%H%M%S)"
echo "[backup] app-postgres"
docker compose exec -T app-postgres pg_dump -U hpagent_migrate -d hpagent -F c > "$OUT/app-$stamp.dump"
echo "[backup] hindsight-postgres"
docker compose exec -T hindsight-postgres pg_dump -U hindsight -d hindsight -F c > "$OUT/hindsight-$stamp.dump"
echo "[backup] workspace"
tar czf "$OUT/workspace-$stamp.tar.gz" .data/workspace
echo "[backup] done → $OUT"
```

---

# 4. 回滚（怎么回滚）

Phase G 使用 **backup + forward migration + application rollback**（§25），
不要求每个 migration 写 DOWN。

## 4.1 新应用有问题但 schema 兼容

```bash
docker compose --profile web stop hpagent-api web-gateway hpagent
# 切回上一个 git SHA / 上一版镜像
git checkout <previous-SHA>
docker compose --profile web build
docker compose --profile web up -d
```

## 4.2 migration 本身破坏数据

```bash
docker compose --profile web stop
# 恢复 App PostgreSQL 备份
docker compose up -d app-postgres   # 仅启动 DB
docker cp backups/app-<date>.dump hpagent_web-app-postgres-1:/tmp/app.dump
docker compose exec -T app-postgres pg_restore -U hpagent_migrate -d hpagent --clean --if-exists /tmp/app.dump
# 恢复上一版应用
git checkout <previous-SHA>
docker compose --profile web build && docker compose --profile web up -d
```

## 4.3 绝对禁止

```bash
docker compose down -v    # 会删除 volume，可能直接删掉用户数据
```

`-v` 不是恢复方式。恢复路径永远从 §3 的备份开始。

---

# 5. Secret 收口（§11）

生产 secret 至少包括：

```text
WEB_CURSOR_SECRET
WEB_SESSION_TOKEN_PEPPER
WEB_CSRF_SIGNING_KEY
WEB_CREDENTIALS_JSON
HPAGENT_WORKER_PASSWORD
HPAGENT_API_PASSWORD
HPAGENT_MIGRATE_PASSWORD
MINIMAX_*
SILICONFLOW_*
HINDSIGHT_*
```

原则：secret → environment / secret file，绝不 commit 到 Git。`.env` 已足够 MVP，
但 `.env` 必须保持 `.gitignore`。生产 secret 长度必须 ≥ 32 bytes（API 启动校验）。

---

# 6. Outbox / Memory 运维（§26）

## 6.1 查询 Outbox 状态

```bash
docker compose exec -T app-postgres psql -U hpagent_worker -d hpagent -c "
  SELECT status, count(*) FROM outbox_events GROUP BY status ORDER BY status;"
```

结果示例：

```text
   status    | count
-------------+-------
 pending     |    12
 processing  |     0
 processed   | 10420
 dead_letter |     2
```

## 6.2 查看 retain_memory dead-letter（含 run_id / attempt_count / last_error）

```bash
docker compose exec -T app-postgres psql -U hpagent_worker -d hpagent -c "
  SELECT outbox_event_id, run_id, attempt_count, last_error_code,
         left(last_error_message, 200) AS last_error, updated_at
  FROM outbox_events
  WHERE event_type='retain_memory' AND status='dead_letter'
  ORDER BY updated_at DESC LIMIT 20;"
```

## 6.3 重放 retain_memory（Run ID → PostgreSQL → MemoryRetentionService）

**禁止**手工用任意正文 / account_id / bank_id 直接写 Hindsight（§26）。

标准重放：把 dead-letter 事件重新置为 `pending`，由 `MemoryRetentionWorker` 从
PostgreSQL 重建 document 并写入 Hindsight：

```sql
-- 把指定 outbox_event_id 的 dead-letter 事件重新排队
-- （注意：仅当 Hindsight 已恢复、且该 Run 仍处于可重放的 completed 终态时执行）
UPDATE outbox_events
SET status='pending', attempt_count=0, locked_at=NULL, locked_by=NULL,
    available_at=now(), last_error_code=NULL, last_error_message=NULL,
    updated_at=now()
WHERE outbox_event_id='<uuid>'
  AND event_type='retain_memory'
  AND status='dead_letter';
```

`MemoryRetentionWorker`（随 `hpagent` Worker 运行）会自动 claim 并处理。
完成后：

```bash
docker compose exec -T app-postgres psql -U hpagent_worker -d hpagent -c "
  SELECT run_id, status, attempt_count, processed_at
  FROM outbox_events WHERE run_id='<uuid>';"
```

## 6.4 全部 dead-letter 统一重放（谨慎）

```sql
UPDATE outbox_events
SET status='pending', attempt_count=0, locked_at=NULL, locked_by=NULL,
    available_at=now(), last_error_code=NULL, last_error_message=NULL, updated_at=now()
WHERE status='dead_letter' AND event_type='retain_memory';
```

---

# 7. Stuck Run 处理（§2 故障恢复路径）

```text
故障 → 定位服务 → 查看状态/日志 → 判断是否影响 canonical state → 恢复/重启/rollback
```

## 7.1 定位

```bash
# Run 状态
docker compose exec -T app-postgres psql -U hpagent_worker -d hpagent -c "
  SELECT run_id, status, workflow_id, started_at, updated_at,
         failure_code, left(failure_message, 200) AS failure_message
  FROM runs
  WHERE status IN ('queued','running','cancelling')
  ORDER BY updated_at;"

# Temporal 侧 Workflow 状态
docker compose exec -T temporal tctl --address localhost:7233 workflow list \
  --query "WorkflowType='WebRunWorkflow'" --pagesize 20
```

## 7.2 判断

- **Run 长期 `queued` 且没有对应 outbox `start_run` 事件** → Dispatcher 未消费，看
  Worker 日志 / Outbox lease。
- **Run 长期 `running` 但 Temporal Workflow 不存在** → Worker 中途重启，`WebRunReconciler`
  会把没有活跃 Workflow 的 Run 恢复到合法终态（failed）或触发 cancel。
- **Outbox 事件 `processing` 且 lease 过期** → `run_web_outbox_recovery_loop` 会自动
  回收 lease 回 `pending`，无需手工干预。

## 7.3 处理

1. 等一个 Reconciler 周期（默认 ~5s）观察是否自愈。
2. 仍未恢复：
   - Worker 日志 `docker compose logs --tail 200 hpagent` 定位异常。
   - 确认 PostgreSQL 可访问；Redis/Hindsight 故障允许降级但绝不允许伪成功。
3. 极端情况（reconciler 也无法恢复）：
   ```bash
   docker compose restart hpagent   # Worker 重启，Temporal/Reconciler 恢复 Run
   ```
   若 Run 对应的 Temporal Workflow 永久卡死：
   ```bash
   docker compose exec -T temporal tctl --address localhost:7233 workflow terminate \
     --workflow_id <web-run-{run_id}> --reason "manual ops: stuck run"
   ```
   Reconciler 会把 Run 标为 `failed`，用户在 Web 可 retry。

---

# 8. Identity 运维（§27）

## 8.1 bootstrap_identity 标准流程

```bash
PYTHONPATH=src python scripts/bootstrap_identity.py
```

幂等。执行后确认 Web 与 QQ 绑定到**同一个** account_id：

```bash
docker compose exec -T app-postgres psql -U hpagent_worker -d hpagent -c "
  SELECT b.provider, b.normalized_subject_id, b.account_id, b.status
  FROM identity_bindings b ORDER BY b.account_id, b.provider;"
```

预期：

```text
 provider |  normalized_subject_id  | account_id | status
----------+-------------------------+------------+--------
 web      | <web-subject>           | A          | active
 qq       | <qq-subject>            | A          | active
```

## 8.2 多账号冲突必须 FAIL（Case E）

```text
Web → Account A
QQ  → Account B
```

必须**失败**，**不得**自动 merge。`accounts.json` 时代的 `merge-account.py` 是运维
工具，不是自动行为。

## 8.3 故障表现

| 场景 | 用户看到 |
|---|---|
| QQ 未绑定 | “账号尚未绑定”提示 |
| PostgreSQL 不可访问（IdentityResolutionUnavailable） | “服务不可用”提示，**不是**“尚未绑定” |
| disabled Account | 无法解析 |

---

# 9. 常见故障与恢复速查

| 故障 | 系统行为 | 恢复 |
|---|---|---|
| Redis 挂 | SSE 降级 / GET polling，Web 基本功能仍工作；数据库数据完整 | `docker compose restart redis` |
| Hindsight 挂 | Web Run 仍 completed；retain_memory → pending/retry/退避；恢复后 MemoryRetentionWorker eventually 处理 | `docker compose restart hindsight`；如出现 dead-letter 按 §6.3 重放 |
| Worker 中途重启 | Run 不永久 active；Temporal/Reconciler 恢复到合法终态 | 无需操作或 `docker compose restart hpagent` |
| PostgreSQL 不可访问 | API / Identity 关键路径**明确失败**（不伪装空数据/成功） | 恢复 DB 后按需重试 |
| migration 失败 | api / worker 不启动（depends_on completed_successfully） | 修复 migration 或按 §4.2 回滚 |

---

# 10. 验收 Checklist（§32）

```text
Infrastructure:  app-postgres/migration/redis/temporal/hindsight/api/worker/gateway 全部 healthy
Web:             HTTPS / login / create Conversation / send / SSE / refresh / stop / retry / multi-tab busy
Agent:           Web real Agent + QQ real Agent 同进程拓扑，isolation gate 通过
Identity:        Web+QQ 同 Account；unbound → 未绑定；DB 不可用 → 服务不可用；disabled 不可解析
Memory:          Web→Web recall / QQ→Web / Web→QQ；跨 Account 不可 recall；短期 Conversation 隔离
Recovery:        Redis 降级 / Hindsight 降级 / Worker 重启 / backup / restore 均已测试
```
