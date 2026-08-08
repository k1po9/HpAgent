# HpAgent Web Phase G — MVP 发布、生产部署与架构收口

> Version: MVP Fast Track v1.0
> 前置状态：Phase A～F 已完成
> Phase G 性质：Release / Deployment / Operations Hardening
> 核心原则：**不新增业务能力，只解决“可上线、可恢复、可解释”。**

---

# 1. Phase G 的定位

Phase G 不再修改 HpAgent 的核心 Agent 能力。

到 Phase F 结束时，系统已经具备：

* PostgreSQL Conversation / Message / Run 真相源
* Web API
* Web Frontend
* SSE 实时事件
* Temporal durable orchestration
* QQ / Web 共用 Agent Execution
* Workspace Account 级隔离
* PostgreSQL Account / IdentityBinding
* QQ / Web 跨端统一身份
* Hindsight Account 级长期记忆
* Web `retain_memory` Outbox
* MemoryRetentionWorker
* QQ / Web 跨端 recall

Phase G 只回答最后几个问题：

```text
浏览器到底访问哪个入口？
        ↓
生产环境应该启动哪些容器？
        ↓
哪些端口可以暴露公网？
        ↓
哪些配置错了必须拒绝启动？
        ↓
升级数据库之前怎么备份？
        ↓
服务挂了以后怎么恢复？
        ↓
什么测试通过以后才能说 MVP 可发布？
```

因此 Phase G 本质是：

```text
A-F
“系统功能完成”

        ↓

Phase G
“系统可以交付”
```

---

# 2. Phase G 最终目标

完成 Phase G 后，应形成一个可以重复执行的发布流程：

```text
git commit
   ↓
CI
   ↓
production build
   ↓
database migration
   ↓
services start
   ↓
health / smoke check
   ↓
MVP available
```

同时发生故障时必须存在明确恢复路径：

```text
故障
 ↓
定位服务
 ↓
查看状态 / 日志
 ↓
判断是否影响 canonical state
 ↓
恢复 / 重启 / rollback
```

而不是：

```text
“Docker 好像挂了”
→ 手工删容器
→ 手工删 volume
→ 再试一次
```

---

# 3. Phase G 的设计原则

## G-P1：不重构已经工作的 A-F

Phase G 默认不允许重构：

* Conversation / Message / Run schema
* Temporal Workflow
* AgentExecutionFacade
* SSE contract
* Memory retention architecture
* IdentityBinding architecture
* Hindsight bank strategy
* Frontend state model

除非发现真实 release blocker。

---

## G-P2：不增加新的微服务

当前 MVP 已经有足够清晰的组件边界。

不要为了“生产化”增加：

* 独立 Memory Service
* 独立 Identity Service
* 独立 SSE Service
* Kafka
* RabbitMQ
* Kubernetes
* Helm
* Service Mesh

当前：

```text
PostgreSQL
Redis
Temporal
Hindsight
hpagent-api
hpagent main worker
web gateway
```

已经足够。

---

## G-P3：继续保持单 Agent Worker 拓扑

当前 Workspace mode：

```text
single_process_account_lock
```

因此：

```text
QQ Agent
+
Web Agent
```

必须继续运行在：

```text
同一个 hpagent Worker 进程
```

共享：

```text
AccountLockRegistry
```

Phase G 不允许重新创建：

```text
hpagent-web-agent-worker
```

第二个 Agent 进程。

否则会破坏 Phase C/D 已建立的 Workspace 隔离模型。

---

## G-P4：PostgreSQL 仍然是业务真相源

任何部署或恢复逻辑都必须保持：

```text
Conversation
Message
Run
Session
IdentityBinding
Outbox
```

以 PostgreSQL 为 canonical truth。

Redis：

```text
SSE / transient realtime / cache
```

仍然允许丢失。

因此 Redis 故障：

```text
≠
业务数据丢失
```

---

## G-P5：允许降级，不允许伪造成功

例如：

```text
Redis 挂
→ SSE degrade / polling
→ Web 基本功能仍工作
```

```text
Hindsight 挂
→ Run 仍 completed
→ retain_memory pending / retry
```

但是：

```text
PostgreSQL 不可访问
→ API / Identity 等关键路径必须明确失败
```

不能伪装为：

```text
空数据
未绑定
成功
```

---

# 4. Phase G 不做什么

以下全部不属于 Phase G MVP：

* Kubernetes
* Helm
* Terraform
* Prometheus + Grafana 完整平台
* ELK / Loki 集群
* 分布式 tracing 平台
* 自动扩缩容
* 多 Agent Worker 横向扩容
* `session_worktree`
* Multi-region
* 蓝绿发布平台
* Canary 系统
* 自动 Account merge
* Web 自助 QQ 绑定
* Memory 管理 UI
* 管理员后台
* Hindsight 数据迁移系统
* QQ retain durable Outbox 改造
* 大规模性能压测平台

这些可以留在 P1/P2。

---

# 5. 最终生产拓扑

Phase G 的目标运行结构：

```text
                         Internet
                            │
                         HTTPS
                            │
                            ▼
                    ┌─────────────┐
                    │ Web Gateway │
                    │             │
                    │ React static│
                    │ /api proxy  │
                    └──────┬──────┘
                           │
                           ▼
                     hpagent-api
                           │
                 ┌─────────┴─────────┐
                 │                   │
                 ▼                   ▼
          PostgreSQL                Redis
       canonical truth          realtime only


QQ / Web Run
     │
     ▼
┌──────────────────────────────────┐
│          hpagent Worker          │
│                                  │
│ QQ Worker                        │
│ Web lifecycle Worker             │
│ Web Agent Worker                 │
│ Web Dispatcher                   │
│ Web Reconciler                   │
│ MemoryRetentionWorker            │
│ Scheduler                        │
│ AccountLockRegistry              │
└──────────────┬───────────────────┘
               │
       ┌───────┼─────────┐
       ▼       ▼         ▼
 PostgreSQL  Temporal  Hindsight
```

关键点：

```text
Web Gateway
```

是唯一浏览器入口。

而：

```text
hpagent-api
PostgreSQL
Redis
Temporal
Hindsight
```

不是面向互联网的公开业务入口。

---

# 6. G-01：增加 Production Web Gateway

## 6.1 目标

当前 React/Vite 已经可以：

```text
npm run build
```

但浏览器产品还需要真正的 production serving layer。

新增：

```text
web/Dockerfile
```

以及：

```text
web/nginx.conf
```

或：

```text
deploy/nginx.conf
```

推荐使用：

```text
Node build stage
      ↓
npm ci
npm run build
      ↓
Nginx runtime stage
      ↓
/usr/share/nginx/html
```

不要在生产环境运行：

```text
vite dev
npm run dev
```

---

## 6.2 Gateway 职责

Gateway 只做三件事：

### A. React 静态资源

```text
/
→ index.html

/assets/*
→ JS / CSS
```

SPA route fallback：

```text
try_files
→ index.html
```

---

### B. `/api/` Reverse Proxy

浏览器：

```text
https://hpagent.example.com/api/...
```

Gateway：

```text
/api/
   ↓
hpagent-api:8080
```

这样保持：

```text
Frontend
+
API
=
same origin
```

减少：

* CORS
* Cookie domain
* CSRF origin

复杂度。

---

### C. SSE Streaming

Nginx 对 SSE 路由必须：

```text
proxy_buffering off
```

并允许长连接。

不能因为 Gateway buffering 导致：

```text
Agent 已经产生事件
       ↓
Nginx 一直缓存
       ↓
浏览器几十秒后一次收到
```

---

# 7. HTTPS 边界

生产 Web 必须通过：

```text
https://
```

访问。

原因包括：

* Secure Cookie
* `__Host-` Cookie
* CSRF / Origin boundary
* Browser security

生产：

```text
WEB_PUBLIC_ORIGIN
```

必须是最终用户真正访问的 HTTPS Origin：

```text
https://agent.example.com
```

不要配置成：

```text
http://hpagent-api:8080
```

因为那是内部服务地址，不是 public origin。

Phase G 不要求实现完整 ACME/证书平台。

TLS 可以由：

* Nginx
* Caddy
* Cloud provider
* Cloudflare
* 外部 reverse proxy

提供。

但 release acceptance 必须最终从真实 HTTPS URL 完成一次登录与聊天。

---

# 8. G-02：生产配置与启动门禁

Phase G 不新增一堆 Feature Flag。

继续使用已经存在的配置。

重点配置：

```text
HPAGENT_ENV
WEB_PUBLIC_ORIGIN

WEB_REAL_AGENT_ENABLED
WEB_REAL_AGENT_GATE_VERSION
WEB_UNIFIED_ACCOUNT_ENABLED

WEB_FAKE_EXECUTOR_ENABLED

WORKER_DATABASE_URL
APP_DATABASE_URL
MIGRATION_DATABASE_URL

REDIS_URL

WORKSPACE_ISOLATION_MODE
```

---

# 9. Production 必须满足的配置

推荐生产基线：

```text
HPAGENT_ENV=production

WEB_REAL_AGENT_ENABLED=true
WEB_REAL_AGENT_GATE_VERSION=c-07-v1

WEB_UNIFIED_ACCOUNT_ENABLED=true

WEB_FAKE_EXECUTOR_ENABLED=false

WORKSPACE_ISOLATION_MODE=single_process_account_lock
```

同时：

```text
Agent Worker replicas = 1
```

---

# 10. Production Fail-Closed 规则

以下情况必须拒绝启动，而不是偷偷降级。

## 10.1 Fake executor

如果：

```text
HPAGENT_ENV=production
```

且：

```text
WEB_FAKE_EXECUTOR_ENABLED=true
```

直接：

```text
startup failure
```

---

## 10.2 Public origin

生产：

```text
WEB_PUBLIC_ORIGIN
```

必须存在，并使用：

```text
https://
```

---

## 10.3 Real Agent gate

如果：

```text
WEB_REAL_AGENT_ENABLED=true
```

但：

```text
WEB_REAL_AGENT_GATE_VERSION
```

不匹配冻结版本：

```text
c-07-v1
```

Worker 拒绝启动。

继续沿用现有 Phase C/D gate。

---

## 10.4 Workspace topology

如果：

```text
WORKSPACE_ISOLATION_MODE=single_process_account_lock
```

必须保证：

```text
agent_worker_replicas == 1
```

不得启动第二个独立 Web Agent Worker。

---

## 10.5 Unified Account

如果：

```text
WEB_UNIFIED_ACCOUNT_ENABLED=true
```

则必须存在：

```text
WORKER_DATABASE_URL
```

QQ 不允许 fallback：

```text
accounts.json
```

---

# 11. Secret 收口

生产环境不得继续依赖 development 默认 secret。

至少包括：

```text
WEB_CURSOR_SECRET

WEB_SESSION_TOKEN_PEPPER

WEB_CSRF_SIGNING_KEY

WEB_CREDENTIALS_JSON

HPAGENT_WORKER_PASSWORD
HPAGENT_API_PASSWORD
HPAGENT_MIGRATE_PASSWORD
```

以及模型：

```text
MINIMAX_*
SILICONFLOW_*
HINDSIGHT_*
```

原则：

```text
secret
→ environment / secret file
```

不得：

```text
commit 到 Git
```

Phase G 不引入 Vault。

`.env` 已足够 MVP 使用，但：

```text
.env
```

必须保持 `.gitignore`。

---

# 12. G-03：Compose 部署收口

现有 Compose 已经具备：

```text
app-postgres
hpagent-migrate
hpagent-api
hpagent
redis
temporal
hindsight
hindsight-postgres
```

Phase G 在此基础上增加：

```text
web-gateway
```

不要重新设计所有容器。

---

# 13. Migration 启动顺序

保持：

```text
app-postgres healthy
        ↓
hpagent-migrate
        ↓
migration success
        ↓
hpagent-api / hpagent
```

必须保证：

```text
Schema migration
```

在：

```text
API / Worker 接收业务
```

之前结束。

如果 migration failed：

```text
hpagent-api
hpagent
```

不得继续启动。

---

# 14. 公网端口原则

正式部署时，原则是：

```text
Internet
   ↓
Web Gateway
```

而不是：

```text
Internet
 ├── PostgreSQL
 ├── Redis
 ├── Temporal
 ├── Hindsight
 └── API
```

开发环境可以保留：

```text
localhost:5434
localhost:6379
localhost:8001
...
```

用于调试。

生产至少绑定：

```text
127.0.0.1
```

或者完全不 publish host port。

唯一公开业务入口应是 Gateway HTTPS。

QQ 通信确实需要的 host port 可继续保留，但不要因为 Web 上线额外公开数据库/Redis。

---

# 15. Health Check 设计

不要建立复杂 service discovery。

MVP 只需要最低限度 health。

## hpagent-api

建议：

```text
GET /healthz
```

代表：

```text
API process alive
```

可再提供：

```text
GET /readyz
```

检查：

```text
PostgreSQL 可访问
```

Redis 不应成为 API readiness 的强依赖。

因为：

```text
Redis unavailable
```

理论上 Web 可以：

```text
SSE degrade
→ GET polling
```

---

## Hindsight

继续使用其已有：

```text
/health
```

---

## PostgreSQL

继续：

```text
pg_isready
```

---

## Main Worker

MVP 不为了 health check 再开 HTTP server。

判断：

```text
Docker process running
+
startup log
+
Temporal real Run smoke
```

即可。

---

# 16. G-04：Release CI 收口

当前 CI 已经存在：

```text
lint/typecheck

unit

PostgreSQL contracts

API contracts

Workspace isolation

Temporal integration

Temporal replay

Frontend lint
Frontend typecheck
Frontend unit
Frontend production build

Playwright E2E
```

因此 Phase G：

**不要重新写 CI。**

---

# 17. Phase F 测试加入 CI

将 Phase F 新增测试明确纳入现有门禁，包括：

```text
PostgresAccountService
bootstrap_identity
unbound identity
IdentityResolutionUnavailable

Hindsight retain_document

QQ retention document

MemoryRetentionService
MemoryRetentionWorker

Web background tasks
```

保证：

```text
Phase F
```

不是只在开发机手动执行。

---

# 18. CI 分成两层即可

## PR Fast Gate

每个 PR：

```text
lint
typecheck
unit
postgres
frontend build/unit
```

目标：

```text
快速发现明显错误
```

---

## Release Gate

准备发布时：

```text
PR Fast Gate
+
Temporal integration
+
Playwright browser E2E
+
production build
+
production compose smoke
```

不要做几十套重复矩阵。

---

# 19. Production Compose Smoke

Phase G 应新增一个很重要、但很小的测试：

```text
production deployment smoke
```

它验证真正部署组合，而不是单个 Python module。

建议流程：

```text
docker compose build
        ↓
docker compose up
        ↓
migration completed
        ↓
PostgreSQL healthy
Redis healthy
Temporal healthy
Hindsight healthy
API healthy
Gateway healthy
Worker running
        ↓
login
        ↓
create Conversation
        ↓
send one message
        ↓
Run reaches completed
        ↓
GET history contains final answer
```

不要求验证模型回答质量。

只验证：

```text
整条链路能够工作
```

---

# 20. G-05：最低限度故障 Smoke

原总计划包含很大的 fault injection matrix。

MVP 不需要全部实现。

只保留 4 个真正关键场景。

## Case 1：Redis 故障

验证：

```text
Redis unavailable
        ↓
数据库数据仍完整
        ↓
前端可以 degrade/poll
```

---

## Case 2：Hindsight 故障

验证：

```text
Web Run
→ completed
```

同时：

```text
retain_memory
→ pending / retry
```

Hindsight 恢复：

```text
MemoryRetentionWorker
→ eventually retained
```

---

## Case 3：Worker 中途重启

执行中：

```text
restart hpagent
```

验证：

```text
Run 不永久 active
```

Temporal/Reconciler 最终能恢复到合法状态。

---

## Case 4：PostgreSQL 不可访问

验证：

```text
API / Identity
```

明确失败。

特别是 QQ：

```text
IdentityResolutionUnavailable
```

而不是：

```text
账号尚未绑定
```

---

# 21. 不做性能平台

Phase G 不增加：

```text
k6 cluster
JMeter
Locust farm
Grafana dashboard
```

只记录一个 MVP baseline。

例如：

```text
Conversation history:
100 messages

Concurrent browser:
1-3

Concurrent Conversations:
2

SSE:
normal

Long Agent Run:
1
```

记录：

```text
页面可正常打开
发送无明显阻塞
SSE 正常
最终 Run 正确
```

即可。

当前项目不是百万用户 SaaS。

性能优化必须由真实瓶颈驱动。

---

# 22. G-06：Backup / Restore / Rollback Runbook

新增：

```text
docs/operations/web-release.md
```

内容必须能回答：

```text
怎么发布？
怎么备份？
怎么回滚？
怎么查看 dead-letter？
怎么重新处理 retain_memory？
怎么处理 stuck Run？
```

---

# 23. Backup 最小集合

## A. App PostgreSQL

必须备份。

包含：

```text
accounts
identity_bindings
conversations
messages
runs
sessions
outbox_events
workflow_executions
```

推荐使用：

```text
pg_dump
```

---

## B. Workspace

备份：

```text
.data/workspace
```

因为这里包含用户实际文件和 Git workspace。

---

## C. Hindsight PostgreSQL

长期记忆存在：

```text
hindsight-postgres
```

因此正式数据环境应能够：

```text
pg_dump hindsight
```

---

## D. Temporal

Temporal DB 保存 Workflow history。

MVP 不要求每次普通 deploy 都手工备份 Temporal。

但重大升级前，应记录其 volume / PostgreSQL 恢复方式。

---

## E. Redis

不要求备份。

因为 Redis 定义仍然是：

```text
transient state
```

---

# 24. 发布前备份流程

建议：

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

---

# 25. Rollback 原则

Phase G 不要求给每个 migration 都写：

```text
DOWN migration
```

MVP 使用：

```text
backup + forward migration + application rollback
```

如果新应用有问题但 schema 兼容：

```text
stop new version
↓
start previous git/image
```

如果 migration 本身破坏数据：

```text
stop services
↓
restore PostgreSQL backup
↓
restore previous application
```

绝对禁止所谓：

```text
docker compose down -v
```

作为“恢复方式”。

因为：

```text
-v
```

可能直接删除用户数据。

---

# 26. Outbox / Memory 运维

Runbook 至少说明如何查询：

```text
pending
processing
processed
dead_letter
```

的 Outbox。

特别是：

```text
retain_memory
```

必须能够定位：

```text
run_id
attempt_count
last_error
```

重放必须：

```text
Run ID
→ PostgreSQL
→ MemoryRetentionService
→ reconstruct document
```

禁止管理员手工传：

```text
任意正文
任意 account_id
任意 bank_id
```

去写 Hindsight。

---

# 27. Identity 运维

Runbook 必须记录：

```text
scripts/bootstrap_identity.py
```

标准流程。

包括：

```text
Web subject
QQ channel
QQ subject
```

执行后确认：

```text
Web binding
QQ binding
       ↓
same account_id
```

以及 Case E：

```text
Web → Account A
QQ  → Account B
```

必须：

```text
FAIL
```

不得自动 merge。

---

# 28. G-07：架构文档收口

Phase G 最后更新架构 README。

重点不是写很多新文档。

而是把：

```text
“设计上准备这样做”
```

改成：

```text
“当前系统实际上就是这样运行”
```

---

# 29. 最终架构文档必须明确的事实

至少明确：

### Canonical state

```text
PostgreSQL
```

---

### Realtime projection

```text
Redis + SSE
```

---

### Durable orchestration

```text
Temporal
```

---

### Long-term memory

```text
Hindsight
bank = hpagent-u-{account_id}
```

---

### Identity

```text
Account
   ↑
IdentityBinding
 ├── Web
 └── QQ
```

---

### Workspace isolation

```text
single_process_account_lock
```

---

### Agent topology

```text
1 main Agent process

QQ
+
Web
```

共享：

```text
AccountLockRegistry
```

---

### Web memory

```text
web-run:{run_id}
```

---

### QQ memory

```text
qq-execution:{execution_id}
```

---

### Web retain consistency

```text
Transactional Outbox
+
eventual consistency
```

---

# 30. 保留的 Legacy / P1 技术债

Phase G 不应该假装项目已经完美。

最终文档明确记录：

## P1-01

QQ retention 仍然是：

```text
best effort
```

不是 Web 那样的 durable Outbox。

---

## P1-02

当前只实现：

```text
single_process_account_lock
```

`session_worktree` 尚未实现。

---

## P1-03

没有 Web 自助：

```text
QQ identity binding
```

当前由管理员 bootstrap。

---

## P1-04

没有历史：

```text
accounts.json
→ PostgreSQL
```

自动迁移。

---

## P1-05

没有旧：

```text
Hindsight bank
```

自动 merge。

---

## P1-06

Memory 没有管理 UI。

---

# 31. G-08：最终 MVP Acceptance

Phase G 最后只做一次统一验收。

不要再建立巨大测试矩阵。

---

# 32. 最终验收 Checklist

## Infrastructure

```text
[ ] app-postgres healthy
[ ] migration success
[ ] Redis healthy
[ ] Temporal healthy
[ ] Hindsight healthy
[ ] hpagent-api healthy
[ ] hpagent Worker alive
[ ] Web Gateway accessible
```

---

## Web

```text
[ ] HTTPS
[ ] login works
[ ] create Conversation
[ ] send message
[ ] SSE updates
[ ] refresh works
[ ] stop works
[ ] retry works
[ ] multi-tab busy works
```

---

## Agent

```text
[ ] Web real Agent executes
[ ] QQ real Agent executes
[ ] QQ + Web use same process topology
[ ] Workspace isolation gate passes
```

---

## Identity

```text
[ ] Web + QQ binding → same Account
[ ] Unbound QQ → 未绑定提示
[ ] DB unavailable → 服务不可用提示
[ ] disabled Account cannot resolve
```

---

## Memory

```text
[ ] Web → new Web Conversation recall
[ ] QQ → Web recall
[ ] Web → QQ recall
[ ] different Account cannot recall
[ ] short-term Conversation remains isolated
```

---

## Recovery

```text
[ ] Redis degradation tested
[ ] Hindsight degradation tested
[ ] Worker restart tested
[ ] backup command tested
[ ] restore procedure documented
```

---

# 33. Phase G 推荐实施顺序

严格按下面顺序即可。

## Step 1

Production Web Gateway：

```text
React build
+
Nginx
+
/api proxy
+
SSE proxy config
```

---

## Step 2

Production configuration hardening：

```text
HTTPS origin
production secrets
fake executor gate
workspace gate
unified account gate
```

---

## Step 3

Compose 收口：

```text
web-gateway
health checks
startup ordering
public port exposure
```

---

## Step 4

把 Phase F tests 纳入 CI。

---

## Step 5

增加 production compose smoke。

---

## Step 6

执行 4 个最小 fault smoke：

```text
Redis
Hindsight
Worker restart
PostgreSQL unavailable
```

---

## Step 7

写：

```text
docs/operations/web-release.md
```

---

## Step 8

更新最终架构 README / deployment diagram 文本。

---

## Step 9

运行一次完整 release acceptance。

---

## Step 10

生成：

```text
docs/web/phase-reports/phase-g-report.md
```

Phase G 结束。

---

# 34. 推荐新增文件

建议新增：

```text
web/Dockerfile
web/nginx.conf

docs/web/phase-g.md
docs/operations/web-release.md
docs/web/phase-reports/phase-g-report.md
```

可能新增：

```text
scripts/release-smoke.sh
scripts/backup.sh
```

如果简单 shell 已足够，不要引入新的部署框架。

---

# 35. 推荐修改文件

主要：

```text
docker-compose.yaml
.github/workflows/ci.yml
src/web_api/config.py
src/web_api/app.py
```

只有确实需要 health endpoint / production startup validation 时修改 API。

可能：

```text
src/orchestration/config.py
src/orchestration/worker.py
```

但只用于补缺失的 production gate。

不要重新设计 Worker。

---

# 36. 明确禁止修改的核心区域

没有 blocker 时，不修改：

```text
persistence schema
WebRunWorkflow
AgentExecutionFacade
brain_action_loop
SSE event contract
MemoryRetentionService semantic
PostgresAccountService identity model
Hindsight bank model
assistant-ui adapter architecture
```

Phase G 是 release phase，不是 architecture rewrite phase。

---

# 37. 最小自动测试

Phase G 新增自动测试只需要：

### G-T01

production frontend build success

### G-T02

Gateway SPA fallback works

### G-T03

Gateway `/api` proxy works

### G-T04

Gateway SSE 不 buffering

### G-T05

production fake executor fails closed

### G-T06

invalid Workspace topology fails closed

### G-T07

production migration from empty DB succeeds

### G-T08

production compose smoke succeeds

不需要为每个 Docker 参数写单元测试。

---

# 38. 最小人工验收

只要求一次真实 browser smoke：

```text
HTTPS URL
   ↓
login
   ↓
new Conversation
   ↓
send
   ↓
real Agent
   ↓
SSE
   ↓
completed
   ↓
refresh
   ↓
history correct
```

再测试一次 QQ：

```text
QQ
↓
same Account
↓
real Agent
↓
long-term memory available from Web
```

即可。

---

# 39. Phase G Definition of Done

Phase G 完成不是：

```text
“测试很多”
```

而是满足下面五点。

### 1.

有一个明确的：

```text
production browser entrypoint
```

---

### 2.

错误配置能够：

```text
fail closed
```

而不是错误运行。

---

### 3.

从：

```text
empty environment
```

能够重复执行：

```text
migration
→ startup
→ browser login
→ real Agent Run
```

---

### 4.

发生：

```text
Redis / Hindsight / Worker
```

常见故障时知道：

```text
系统会发生什么
如何恢复
```

---

### 5.

有：

```text
backup
rollback
release checklist
final architecture document
```

---

# 40. Phase G 最终一句话

Phase A～F 解决：

> **HpAgent Web 应该怎样正确运行。**

Phase G 解决：

> **怎样把这套已经正确运行的系统安全、可重复地放到真实环境中，并且出了问题知道怎样恢复。**

这就是 MVP 从：

```text
“项目”
```

变成：

```text
“可交付产品”
```

的最后一步。
