# HpAgent Web Phase G 实施报告

## 1. 结论

Phase G 已按 `docs/web/phase-g.md` 完成 MVP 发布、生产部署与架构收口：新增生产 Web
Gateway（React build + Nginx）、生产配置 fail-closed 门禁、Compose 部署收口、Phase F
测试纳入 CI、网关冒烟脚本、故障 smoke 对照、发布运维 Runbook 与架构 as-built 文档。
**不新增业务能力，只解决“可上线、可恢复、可解释”。**

- `web/Dockerfile`（Node build → Nginx runtime）+ `web/nginx.conf` 已构建并冒烟通过：
  SPA fallback、`/api` + `/auth` 代理、SSE `proxy_buffering off` 全部验证（G-T01~T04）。
- 生产 fail-closed 门禁已落地并有测试：fake executor、非 HTTPS/缺省 `WEB_PUBLIC_ORIGIN`、
  real-Agent gate 版本、unified account 缺 `WORKER_DATABASE_URL`、单进程拓扑。
- `docker-compose.yaml` 增加 `web-gateway`、`hpagent-api` healthcheck、启动顺序
  （migration → API → gateway）与公网端口收口（内部服务全部绑定 `127.0.0.1`）。
- CI 新增 `phase-f-gate`（Phase F 测试显式纳入门禁）与 `phase-g-gateway-smoke`。
- 4 个最小故障 smoke 均有对应自动测试并本地跑通（Redis / Hindsight / Worker 重启 / PG 不可访问）。
- 新增 `docs/operations/web-release.md`（发布/备份/回滚/dead-letter/retain_memory/stuck Run/
  Identity 运维）与 `scripts/backup.sh`、`scripts/release-smoke.sh`、`scripts/gateway-smoke.sh`。
- 架构文档追加 Phase G as-built 事实与 P1 技术债。

## 2. 验证结果

| 项 | 结果 |
|---|---|
| G-T01 production frontend build（Docker 内 `npm ci && npm run build`） | PASS（gateway 镜像构建成功） |
| G-T02 Gateway SPA fallback | PASS |
| G-T03 Gateway `/api` + `/auth` 代理 | PASS |
| G-T04 Gateway SSE 不 buffering（`X-Accel-Buffering: no`，事件即时流式） | PASS |
| Phase F gate（unified identity / retain_document / background tasks / outbox recovery） | 27 passed |
| 全量非 PostgreSQL 测试 | 273 passed, 6 skipped |
| 故障 smoke 覆盖（postgres-backed：memory retention + outbox lease recovery） | 19 passed |
| G-02 生产门禁单测（web_api config + unified account gate） | 17 passed |
| Ruff / mypy（web_api 域内） | 全部通过 |

4 个故障 smoke 的自动测试映射（§20）：

| Case | 测试 |
|---|---|
| Redis 故障 → SSE degrade/poll | `test_sse_http_redis_unavailable_degrades_to_poll` |
| Hindsight 故障 → Run completed + retain pending/retry | `test_hindsight_failure_leaves_run_completed_and_surfaces_accepted_false` |
| Worker 中途重启 → Run 不永久 active | `test_db_025_killed_worker_lease_recovered_by_replacement_dispatcher` + `test_td_005_agent_worker_sigkill_heartbeat_timeout_fails_without_retry` |
| PostgreSQL 不可访问 → 明确失败 | `test_postgres_service_raises_unavailable_not_none_on_db_failure` |

## 3. 实施明细

### 3.1 G-01 / G-03：生产 Web Gateway 与 Compose 收口

- `web/Dockerfile`：多阶段构建（Node 20 build → `nginx:1.27-alpine` runtime），
  生产绝不运行 `vite dev`。`web/.dockerignore` / `web/.npmrc` 加速并增强构建韧性。
- `web/nginx.conf`：React 静态 + SPA fallback（`try_files ... /index.html`）、
  `/assets/` 不可变长缓存、`/api/` 与 `/auth` 反向代理到 `hpagent-api:8080`（同源）、
  `/api/v1/runs/` SSE 路由 `proxy_buffering off` + 3600s 读写超时 + `X-Accel-Buffering: no`。
- `docker-compose.yaml`：
  - 新增 `web-gateway`（`profiles: ["web"]`，默认宿主机 `${WEB_GATEWAY_PORT:-80}`）。
  - `hpagent-api` 增加 `/health/ready` healthcheck（校验 PostgreSQL 可达）。
  - 启动顺序：app-postgres healthy → hpagent-migrate `service_completed_successfully`
    → hpagent-api healthy → web-gateway healthy（§13）。
  - 公网端口收口：app-postgres/redis/hpagent/hpagent-api/temporal/temporal-web/hindsight
    调试端口全部改为绑定 `127.0.0.1`（§14）。

### 3.2 G-02：生产配置与启动门禁

- `src/web_api/config.py`：`HPAGENT_ENV=production` 时 `WEB_PUBLIC_ORIGIN` 必须显式存在、
  必须是 `https://`、不得是开发默认 `https://localhost`；否则启动失败（§10.2）。
- `src/orchestration/web_workers.py`：新增 `validate_unified_account_backend` ——
  `WEB_UNIFIED_ACCOUNT_ENABLED=true` 但缺 `WORKER_DATABASE_URL` 时拒绝启动，
  QQ 绝不回退 `accounts.json`（§10.5）。`src/orchestration/worker.py` 在组装账号服务时调用。
- 既有门禁保持不变：fake executor 生产拒绝（web_api config）、real-Agent gate `c-07-v1`
  （`validate_web_worker_startup`）、workspace 拓扑单 Worker（`validate_workspace_topology`）。

### 3.3 G-04 / G-T：CI 与冒烟

- `.github/workflows/ci.yml` 新增两个 job：
  - `phase-f-gate`：显式运行 Phase F 单元测试（unbound identity / retain_document /
    QQ retention / background tasks / outbox recovery）。需要 PostgreSQL 的 Phase F 文件
    （postgres_account_service / bootstrap_identity / memory_retention）已由
    `phase-a-postgres-contract-tests` 的 `test/web_persistence` glob 覆盖。
  - `phase-g-gateway-smoke`：构建前端 + 运行 `scripts/gateway-smoke.sh`（G-T01~T04）。
- `scripts/gateway-smoke.sh`：构建网关镜像 → stub API → 断言 SPA/代理/SSE。
- `scripts/release-smoke.sh`：真实生产组合全链路冒烟（§19）—— build → up → migration →
  healthy → login → create Conversation → send → Run completed → history 含最终回答。
- `scripts/backup.sh`：App PostgreSQL + Hindsight + Workspace 最小备份集合（§23）。

### 3.4 G-06：运维 Runbook

`docs/operations/web-release.md` 回答：怎么发布（§2）、怎么备份（§3）、怎么回滚（§4，
禁止 `down -v`）、怎么查 dead-letter（§6）、怎么重放 retain_memory（Run ID → PostgreSQL
→ MemoryRetentionService，禁止手工写 Hindsight）、怎么处理 stuck Run（§7）、Identity
运维（§8）、常见故障速查（§9）。

### 3.5 G-07：架构收口

`docs/web/hpagent-web-system-architecture.md` 追加「生产部署现状（Phase G as-built）」：
canonical state / realtime / orchestration / long-term memory / identity / workspace /
agent topology / web·qq memory / retain consistency 事实表 + 生产配置基线 + §20 待决事项
处置 + P1 技术债（P1-01~06）。

## 4. 最终验收 Checklist 对照

```text
Infrastructure:  compose config 校验通过；gateway 镜像构建并冒烟通过；migration→API→gateway 顺序已配置
Web:             gateway SPA/代理/SSE 冒烟通过（G-T01~04）；真实浏览器 HTTPS 验收留待正式发布环境
Agent:           单进程拓扑 + workspace 门禁保持不变（未新增第二个 Web Worker）
Identity:        生产门禁测试通过（unified account fail-closed、public origin、secret 长度）
Memory:          retain/recall 相关 Phase F 测试纳入 CI（phase-f-gate）
Recovery:        4 个故障 case 自动测试全部跑通；backup/restore 脚本与 runbook 就绪
```

## 5. 发布前待办（运行环境）

- 以真实 HTTPS origin 运行一次浏览器 login + 聊天（§38 人工验收）。
- 执行 `scripts/bootstrap_identity.py` 确认 Web + QQ 绑定同一 Account（§8）。
- 发布前运行 `scripts/backup.sh` 并核对备份（§2.2/§24）。

## 6. 未做（明确留待 P1/P2，§4）

Kubernetes / Helm / Terraform / 完整可观测平台 / 自动扩缩容 / 多 Agent Worker /
`session_worktree` / 蓝绿 / Canary / 自动 Account merge / Web 自助 QQ 绑定 /
Memory 管理 UI / 管理员后台 / Hindsight 迁移系统 / QQ durable Outbox / 大规模压测。
