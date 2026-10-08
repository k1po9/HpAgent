# HpAgent

HpAgent 是一个支持 Web 与 QQ 双入口的 AI 助手平台：Main Agent 处理交互，Work 保存持续委托，Run 记录有限执行，Execution 隔离执行上下文。

## 核心能力

- Web 与 QQ 共享统一的 `Account`、`Conversation`、`Message`、`Work` 和 `Run` 状态。
- Work 保存不可变需求 revision、checkpoint、继续条件与累计预算；后台 Work Run 不占聊天槽位，也不创建合成聊天消息。
- 使用 PostgreSQL 事务与 Outbox 可靠接收任务，再交由 Temporal 持久化执行。
- ReAct 与 Plan-and-Execute 策略共享 Context、Brain、Actions、Memory 和 Workspace。
- Hindsight 提供长期记忆，Redis 提供临时协调与缓存。
- 支持本地工具、MCP、Sandbox、账号级长期文件 Workspace 与可选的 Git 代码工作区。
- 长期文件可跨 Conversation 授权使用；Run 冻结候选范围、按需固定版本和物化文件。
- Research 是独立于 Agent 策略的固定证据研究流程。
- 提醒、研究、通用工作和成果构建共用有限 Run 生命周期，策略由服务端注册并冻结。
- Artifact 保存成果版本和生产来源；通知、渠道回执与用户验收分别记账。
- Generic Work 的 root 可进行一次最多三个只读 Subagent 分支委派，共用原 Run / Work 预算。
- 高开销文档规范化由独立的 Temporal Activity Worker 执行。
- Web 使用 AI、空间、任务三个一级入口；执行、文件、任务和 HTML 成果通过统一上下文 Inspector 查看。

## 架构

```text
Web / QQ → Main / Conversation commands ─► chat Run
                   └► Work commands → requirement / wakeup ─► work Run
                                          │
                               PostgreSQL + Outbox
                                          ↓
                               AgentLifecycleWorkflow
                                          ↓
                  deterministic / fixed_workflow / generic_agent
                                          ↓
                  Execution / receipts / Artifact / Workspace save
                                          ↓
                  Run terminal fact + Work completion policy
                                          ↓
                  Web snapshots / SSE + notifications / deliveries
```

运行时、状态归属、能力边界、可靠性和关键时序请参阅[架构文档](docs/architecture/overview.md)。
当前 Work、Run、Execution、调度、交付及治理契约见 [Durable Work V1](docs/architecture/durable-work-v1.md)；设计和各阶段报告是历史依据，当前实现以架构文档与源码为准。
长期文件、目录、授权、版本、保存与 GC 的当前契约见 [Workspace v4.1](docs/architecture/workspace-v4.1.md)。
UI-1～UI-8 重构后的入口、状态隔离、任务四桶与成果交互契约见 [Web 前端架构](docs/architecture/web-ui.md)。

## 快速开始

前置依赖：Git、Docker Engine 和 Docker Compose v2。只有在宿主机开发时才需要 Python 3.11+ 与 Node.js 22+。

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

前端只有 AI、空间、任务三个一级入口。资料授权、研究输出、HTML 成果和执行诊断从当前对象打开，账户设置从头像打开。首次操作和人工校验路径见[功能操作指南](docs/operations/web-workbench.md)。长期上传可选择供当前对话使用；仅保存的文件和旧资料需显式授权，新增资源在下一 Run 生效。

现有旧架构数据库不能按“自动迁移”理解直接升级：054 / 055 / 057 的开发阶段迁移包含空业务存储检查，没有历史 Task / Work 双写或回填层。切换步骤与当前验证限制见[部署说明](docs/operations/deployment.md)及[实施索引](docs/implementation/README.md)。

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
make test-existing       # 非 postgres 标记集合；部分 fixture 仍需独立基础设施
make lint typecheck      # Python 静态检查
make ci-web              # 前端 lint、类型、构建与单元测试
make web-dev             # 宿主机启动 Vite
make agent-benchmark-check
```

数据库测试使用 `make db-up`、`make migrate`、`make test-db` 和 `make test-api`，执行前必须覆盖默认 DSN，指向专用隔离测试库；fixture 会清理业务表。详见[开发环境](docs/development/setup.md)与[测试指南](docs/development/testing.md)。

## 仓库结构

```text
src/          Python 应用、领域、能力、Worker 和 API
web/          React/Vite 前端
config/       当前运行时、模型、Prompt、MCP 和搜索配置
persistence/  PostgreSQL Migration 与数据库初始化
test/         单元、契约、集成测试及 Fixture
scripts/      当前开发、运维、检查和 Benchmark 工具
docs/         当前架构、开发、运维和参考文档
artifacts/    产品验收、审计与 Benchmark 证据
```

## 文档

从 [docs/README.md](docs/README.md) 开始，按以下路径阅读：

1. 理解系统：[架构总览](docs/architecture/overview.md) → [Durable Work V1](docs/architecture/durable-work-v1.md) → [Web 前端架构](docs/architecture/web-ui.md)。
2. 使用与部署：[功能操作指南](docs/operations/web-workbench.md) → [部署](docs/operations/deployment.md)。
3. 开发与复验：[开发环境](docs/development/setup.md) → [测试指南](docs/development/testing.md) → [HTTP API](docs/reference/api.md)。
4. 本次重构：[UI-1～UI-8 交接](docs/implementation/ui-refactor.md) → [保留验收证据](artifacts/product-acceptance/ui-refactor/README.md)。

[实施与验收索引](docs/implementation/README.md)连接 Durable Work、Workspace 及其他历史验收。UI 重构的已执行自动化门禁在记录范围内通过；真机、读屏、真实 QQ 和生产恢复等缺口使最终产品放行条件仍未满足，具体范围以交接为准。
