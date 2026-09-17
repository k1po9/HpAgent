# HpAgent

HpAgent 是一个支持 Web 与 QQ 双入口、以统一对话模型和持久化执行为核心的 AI 助手平台。

## 核心能力

- Web 与 QQ 共享统一的 `Conversation`、`Message`、`Session` 和 `Run` 状态。
- 使用 PostgreSQL 事务与 Outbox 可靠接收任务，再交由 Temporal 持久化执行。
- ReAct 与 Plan-and-Execute 策略共享 Context、Brain、Actions、Memory 和 Workspace。
- Hindsight 提供长期记忆，Redis 提供临时协调与缓存。
- 支持本地工具、MCP、Sandbox、文件处理和账号级 Git Workspace。
- Research 是独立于 Agent 策略的固定证据研究流程。
- Artifact 用于从消息或研究结果生成带版本的交付物。
- 高开销文档规范化由独立的 Temporal Activity Worker 执行。

## 架构

```text
Web / QQ
   ↓
Application / Conversation
   ↓
PostgreSQL + Outbox
   ↓
Temporal Durable Runtime
   ↓
AgentRunWorkflow
   ↓
ReAct / Plan-and-Execute
   ↓
Context / Brain / Actions / Memory / Workspace
   ↓
Committed Result
   ↓
Web SSE / QQ Delivery
```

运行时、状态归属、能力边界、可靠性和关键时序请参阅[架构文档](docs/architecture/overview.md)。

## 快速开始

前置依赖：Git、Docker Engine 和 Docker Compose v2。只有在宿主机开发时才需要 Python 3.11+ 与 Node.js 20+。

1. 准备环境配置：

   ```bash
   cp .env.example .env
   ```

2. 在 `.env` 中填写模型服务地址、模型名称和 API Key。实际模型链由 `config/models.yaml` 声明；至少需要配置其 `chat` 链所使用的 Provider。共享环境或生产部署前必须替换所有 `change-me` 值。

3. 启动 Web 开发拓扑。Compose 会构建应用镜像并启动 PostgreSQL、Redis、Temporal、Hindsight、SearXNG、Gotenberg、API、两个 Worker 和 Vite 前端：

   ```bash
   docker compose --profile web up -d --build
   ```

4. API 和 Worker 启动前，`hpagent-migrate` 一次性服务会自动执行数据库迁移。需要显式执行或重跑时：

   ```bash
   docker compose --profile web up hpagent-migrate
   ```

5. 验证后端并打开前端：

   ```bash
   curl --fail http://127.0.0.1:8080/health/ready
   docker compose --profile web ps
   ```

   打开 <http://127.0.0.1:5173>。生产网关拓扑使用 `docker compose --profile web-prod up -d --build`，访问 `WEB_GATEWAY_PORT` 指定的端口（默认 `80`）。

也可以单独启动 Worker、API 或前端：

```bash
docker compose --profile web up -d hpagent hpagent-document-worker
docker compose --profile web up -d hpagent-api
docker compose --profile web up -d web-dev
```

## 配置

配置入口：

- `.env`：凭据、部署参数、功能开关和 Compose 变量。
- `config/config.yaml`：Temporal、Redis、Hindsight、Workspace、Channel、Sandbox 和 Agent 行为默认值。
- `config/models.yaml`：Provider、环境变量凭据引用、模型链、Embedding、Rerank 和工具检索。
- `config/prompts/`：系统身份、行为指导、环境和工具摘要 Prompt。
- `config/mcp/`：MCP Server 声明。
- `config/searxng/`：Research 搜索服务配置。

详见[配置参考](docs/reference/configuration.md)。

## 日志

### 如何查看日志

统一日志入口为 `scripts/operations/logs.sh`：

```bash
# 实时查看全部运行服务
./scripts/operations/logs.sh

# 只看 API 或 Worker
./scripts/operations/logs.sh --api
./scripts/operations/logs.sh --worker

# 基础设施、最近若干行或指定服务
./scripts/operations/logs.sh --infra
./scripts/operations/logs.sh --api --tail 100 --no-follow
./scripts/operations/logs.sh temporal app-postgres --tail 200

# 停止 Web 拓扑
docker compose --profile web down
```

应用结构化日志同时写入 `.data/logs/hpagent.jsonl` 和 `.data/logs/web-api.jsonl`。如何通过 `run_id`、`conversation_id`、`workflow_id` 和 `operation_id` 串联一次执行，请参阅[日志指南](docs/operations/logging.md)。

## 开发

```bash
make install             # 安装 Python 开发依赖
make test-existing       # 单元测试及非 PostgreSQL 测试
make lint typecheck      # Python 静态检查
make ci-web              # 前端 lint、类型、构建与单元测试
make web-dev             # 宿主机启动 Vite
make agent-benchmark-check
```

数据库测试使用 `make db-up`、`make migrate`、`make test-db` 和 `make test-api`。详见[开发环境](docs/development/setup.md)与[测试指南](docs/development/testing.md)。

## 仓库结构

```text
src/          Python 应用、领域、能力、Worker 和 API
web/          React/Vite 前端
config/       当前运行时、模型、Prompt、MCP 和搜索配置
persistence/  PostgreSQL Migration 与数据库初始化
test/         单元、契约、集成测试及 Fixture
scripts/      当前开发、运维、检查和 Benchmark 工具
docs/         当前架构、开发、运维和参考文档
artifacts/    审计与 Benchmark 证据
```

## 文档

从 [docs/README.md](docs/README.md) 开始。
