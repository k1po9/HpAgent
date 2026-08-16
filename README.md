# HpAgent

HpAgent 是一个同时面向 QQ 与 Web 的单 Agent 应用。两种入口保留各自的接入、状态和输出方式，但共享同一套 `AgentExecutionFacade → DefaultBrainActionLoop` 执行核心。系统使用 Temporal 管理生命周期，PostgreSQL 保存 Web 领域数据与统一身份，Hindsight 保存长期记忆，Sandbox/Workspace 隔离工具执行。

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
- QQ 与 Web 共享单一 Agent loop。
- 统一账号事实源是 PostgreSQL `accounts` 与 `identity_bindings`；QQ 不回退到 JSON 账号库。
- 多 Agent 包仍保留作实验代码，但未接入当前生产运行时。

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
```

## 文档

- [架构文档](docs/architecture/README.md)
- [系统上下文](docs/architecture/single_agent/01_system_context.md)
- [容器架构](docs/architecture/single_agent/02_container.md)
- [组件架构](docs/architecture/single_agent/03_component.md)
- [关键时序](docs/architecture/single_agent/04_sequence.md)
- [配置参考](docs/reference/configuration.md)
- [运行手册](docs/operations/runbook.md)
- [Web 注册与 QQ 绑定](docs/operations/web-registration-and-qq-binding.md)
- [可观测性](docs/operations/observability.md)
- [架构收口报告](docs/architecture/refactor-closure-report.md)
