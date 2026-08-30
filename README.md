# HpAgent

HpAgent 是一个同时面向 QQ 与 Web 的 Agent 应用。两种入口保留各自的接入、状态和输出方式；QQ 与 Web legacy 路径共享 `AgentExecutionFacade → DefaultBrainActionLoop`，Web 还可启用 Temporal durable Agent Workflow。PostgreSQL 保存 Web 领域数据、Agent transcript/operation 与统一身份，Hindsight 保存长期记忆，Sandbox/Workspace 隔离工具执行。

## 当前架构

```text
QQ User ── QQ Channel ── ConversationService ── QQ Workflow ── QQExecutionHost ─┐
                                                                               ├─ AgentExecutionFacade
Browser ── FastAPI ── PostgreSQL/Outbox ── WebRunWorkflow ── WebExecutionHost ─┘
                                                                                      │
                                                                          DefaultBrainActionLoop
                                                                                      │
                                                                    BrainEngine + ActionRuntime
                                                                                      │
                                                               Models / Sandbox / Tools / Memory
```

- QQ 与 Web 不共享 transport 或短期状态模型。
- QQ 与 Web legacy 路径共享单一 Agent loop；Durable Agent MVP 的策略范围固定为
  `react` 与 `plan_and_execute`。
- 统一账号事实源是 PostgreSQL `accounts` 与 `identity_bindings`；QQ 不回退到 JSON 账号库。
- Blackboard 相关 contract、router 扩展点和设计属于 Reserved / Planned，不属于本次
  MVP，也未接入当前生产运行时。

## 技术栈

- Python 3.11、FastAPI、Temporal Python SDK
- PostgreSQL 16、Redis 7
- Hindsight + pgvector
- React 19、TypeScript、Vite、assistant-ui
- Docker Compose、nsjail、Git workspace

## 代码入口

| 路径 | 职责 |
|---|---|
| `src/main.py` | Agent/QQ Worker 入口 |
| `src/bootstrap/qq.py` | QQ Surface、统一 Facade 与应用服务组装 |
| `src/agent_execution/` | QQ/Web Host、Facade、唯一 single-agent loop |
| `src/agent_workflows/` | Durable ReAct、Plan-and-Execute 与策略路由 Workflow |
| `src/agent_activities/` | Context/Model/Tool Activities 与 Agent PostgreSQL data plane |
| `src/orchestration/` | Temporal Workflow、Activity、Web Outbox/Reconciler |
| `src/web_api/` | FastAPI、认证、查询与 SSE |
| `src/web_domain/` | Web 命令、生命周期和 Outbox 领域逻辑 |
| `src/application/` | 对话、归档、记忆、回复等应用服务 |
| `web/` | React Web 客户端 |
| `persistence/` | PostgreSQL migrations 与数据库初始化 |

## 启动

准备 `.env` 后，先启动基础设施：

```bash
docker compose up -d
```

Web 开发模式：

```bash
docker compose --profile web up -d
```

访问 `http://127.0.0.1:5173`。生产 Web profile 使用：

```bash
docker compose --profile web-prod up -d --build
```

需要在无法访问 Docker Hub / GHCR 的服务器部署时，使用[离线镜像部署流程](docs/operations/docker-offline-deployment.md)。

只启动 Agent：

```bash
docker compose --profile agent up -d
```

QQ/NapCat 与 Temporal UI 是独立可选 profile：

```bash
docker compose --profile qq up -d napcat
docker compose --profile tools up -d temporal-web
```

数据库 migration 完成后，用户可在 Web 自助注册并通过 QQ 消息中的真实 sender identity 完成绑定；`scripts/bootstrap_identity.py` 继续保留给管理员。旧 `WEB_CREDENTIALS_JSON` 用户可通过 `scripts/migrate_web_credentials.py` 导入 PostgreSQL。API 与 Worker 必须配置相同的 `QQ_BINDING_CODE_PEPPER`，Worker 缺少 `WORKER_DATABASE_URL` 会拒绝启动。

### Durable Agent 迁移开关

先应用 migration `014_durable_agent_control_plane.sql`，再设置：

```bash
DURABLE_AGENT_ENABLED=true
AGENT_EXECUTION_LEASE_TTL_SECONDS=900
```

关闭该开关时，新 Run 仍启动历史兼容的 `WebRunWorkflow`；开启后新 Run 启动 `DurableWebRunWorkflow`，旧 History 不受影响。发送消息 API 可通过 `agent_strategy` 选择 `react` 或 `plan_and_execute`。

Durable Agent MVP 提供 ReAct 与 Plan-and-Execute 的 durable Workflow 控制流。模型、
工具和计划操作使用稳定 operation ID；只读或具备明确幂等语义的 Tool 可安全重试，
不可确认的非幂等副作用采用 reconciliation / fail-closed，避免自动重复执行。此能力
不等同于承诺所有 Tool exactly-once；Blackboard、QQ durable ownership 统一和
Continue-As-New 均不属于本次 MVP。

## 常用命令

```bash
docker compose ps
docker compose logs -f hpagent hpagent-api
docker compose restart hpagent hpagent-api
make test-existing
make test-db
make test-api
make ci-web
make e2e
make agent-benchmark-check
make agent-benchmark-run
```

`agent-benchmark-check` 只执行环境门禁，不调用模型；`agent-benchmark-run` 会执行低成本
pilot，并在门禁通过后启动 ReAct 与 Plan-and-Execute 的正式配对实验，因此会产生模型调用
费用。Temporal Worker、Activity Worker 与 Transactional Outbox 的独立故障恢复实验入口、
前置条件和证据口径见[基准与故障恢复验证](docs/benchmarks.md)。

## 已验证的恢复边界

仓库包含三组可追溯基准证据：Temporal Workflow Worker/Activity Worker SIGKILL 恢复、
Transactional Outbox 宕机恢复，以及 ReAct/Plan-and-Execute 策略对比。已提交的一轮可靠性
实验共覆盖 50 次 Workflow Worker、20 次 Activity Worker 故障和 30 个 Outbox 请求，均达到
各自预定义的恢复或安全终态；非幂等副作用 ack gap 的安全结果是 `uncertain` fail-closed，
不代表所有外部 Tool 都具备 exactly-once 语义。完整结果、环境与样本限制见
[`artifacts/benchmarks/README.md`](artifacts/benchmarks/README.md)。

## 文档

- [架构文档](docs/architecture/README.md)
- [系统上下文](docs/architecture/single_agent/01_system_context.md)
- [容器架构](docs/architecture/single_agent/02_container.md)
- [组件架构](docs/architecture/single_agent/03_component.md)
- [关键时序](docs/architecture/single_agent/04_sequence.md)
- [配置参考](docs/reference/configuration.md)
- [运行手册](docs/operations/runbook.md)
- [基准与故障恢复验证](docs/benchmarks.md)
- [Web 注册与 QQ 绑定](docs/operations/web-registration-and-qq-binding.md)
- [可观测性](docs/operations/observability.md)
- [架构收口报告](docs/architecture/refactor-closure-report.md)
