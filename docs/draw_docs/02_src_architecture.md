# 02 — HpAgent 源码架构设计书

> 对应绘图文件: `docs/draw/02_src_architecture.excalidraw`（待创建）

**本文档是项目的唯一设计事实来源，所有架构理解应以此为准。**

---

## 1. 项目定义与边界

### 1.1 一句话项目定位

HpAgent 是一个基于「手脑分离」架构的 QQ 智能聊天机器人系统，以 Temporal 工作流为编排引擎、以 Hindsight 为长期记忆服务、以 nsjail 为工具执行沙箱，通过多模型降级链实现高可用 Agentic Loop，并预留多 Agent 协作扩展能力。

### 1.2 核心解决的问题

1. **QQ 消息的智能对话闭环**：接入 NapCat/OneBot v11 协议，将 QQ 群聊/私聊消息转化为完整的 Agentic Loop（感知 → 推理 → 工具调用 → 回复）。
2. **长期记忆的跨会话复用**：通过 Hindsight 将每轮对话的事实提取为向量化记忆，在新消息到达时通过语义检索召回相关记忆，使机器人呈现出记忆连续性。
3. **工具调用的安全隔离**：通过 nsjail 的 PID/NET/FS namespace 隔离本地 Bash 工具执行，防止模型生成的恶意命令损害宿主机。
4. **模型服务的可用性保障**：通过多模型降级链（Mimo → MiniMax → 阿里百炼 → SiliconFlow）在单一模型故障时自动切换，避免服务中断。
5. **会话生命周期的可靠管理**：通过 Temporal 工作流的信号驱动模型实现「一个用户一条工作流」的会话管理，支持空闲超时归档、故障恢复、多消息排队。

### 1.3 明确的技术栈构成

| 层次 | 技术选型 | 版本 |
|------|---------|------|
| 语言 | Python 3.11 (asyncio 全异步) | `python:3.11-slim` |
| 编排引擎 | Temporal SDK + `temporalio` Python 库 | Server 1.26.2, SDK 1.x |
| 内存缓存 | Redis (会话热数据 + PubSub + 群上下文) | `redis:7-alpine` |
| 长期记忆 | Hindsight (pgvector 向量检索 + LLM 事实提取) | `0.6.1` |
| 向量数据库 | PostgreSQL 16 + pgvector 扩展 (Hindsight 存储后端) | `pgvector/pgvector:pg16` |
| 工具向量库 | ChromaDB (工具定义嵌入 + RAG 检索) | 本地持久化客户端 |
| 模型 API | HTTP REST (兼容 Anthropic / OpenAI 两种格式) | httpx 异步客户端 |
| QQ 桥接 | NapCat / OneBot v11 over WebSocket | `mlikiowa/napcat-docker:latest` |
| 沙箱 | nsjail (Google 开源 Linux 沙箱) | `3.4` 静态二进制 |
| 日志 | Python logging 双槽 (彩色控制台 + JSONL 文件) | stdlib |
| 部署 | Docker Compose (9 个容器, 2 个可选 profile) | Compose v3 |

**Python 运行时依赖**（`src/requirements.txt`，10 个包）：

| 包 | 版本要求 | 用途 |
|---|---------|------|
| `httpx` | >=0.27.0 | 异步 HTTP 客户端，所有 LLM API 调用（Mimo/MiniMax/阿里百炼/SiliconFlow）、Hindsight REST API 调用均基于此 |
| `pyyaml` | >=6.0.1 | YAML 配置解析，用于加载 `config.yaml`、`models.yaml`、`prompts/*.yaml`、`agents.yaml` 全部配置文件 |
| `temporalio` | >=1.10.0 | Temporal Python SDK，提供 Workflow 定义（`@workflow.defn`）、Activity 注册（`@activity.defn`）、Worker 运行、信号/查询 API |
| `websockets` | >=13.0 | WebSocket 服务端/客户端，NapCatChannel 作为 WS 服务端监听 0.0.0.0:8082 等待 NapCat 客户端连接；OfficialQQChannel 也使用此库 |
| `langchain-core` | >=0.3.0 | 工具框架基础类型 `BaseTool` / `StructuredTool`，全部 8 个本地工具和 MCP 适配器均继承自此库的工具基类 |
| `pydantic` | >=2.0.0 | 数据验证与 Schema 定义，用于工具参数模型（`BaseModel` + `Field` + `create_model`），所有本地工具的输入参数 Schema 均以此定义 |
| `chromadb` | >=1.0.0 | 向量数据库，工具 RAG 的存储后端。`ToolVectorStore` 将工具名称-描述对向量化存入 ChromaDB，`ToolRetriever` 执行语义检索 |
| `redis` | >=5.0.0 | Redis 异步客户端（hiredis 解析器），用于会话热数据缓存（`hpagent:session:` 键前缀）、群上下文滑动窗口（List）、PubSub 事件总线 |
| `python-dotenv` | >=1.0.0 | 环境变量加载，`main.py` 启动时调用 `load_dotenv()` 自动搜索并加载项目根目录的 `.env` 文件 |
| `croniter` | >=2.0.0 | Cron 表达式解析器，用于 `TaskScheduler` 解析 `cron_expr` 参数的周期性定时任务 |

**容器级系统依赖**（通过 Dockerfile 的 `apt-get install` 安装，非 pip）：

| 依赖 | 用途 |
|------|------|
| `nsjail` 3.4 静态二进制 | OS 级沙箱，从 GitHub Release 下载到 `/usr/local/bin/nsjail`。Bash 工具在启用 nsjail 时通过此二进制执行，实现 PID/NET/FS namespace 隔离 |
| `nodejs` + `npm` | MCP 工具 `stock-sdk`（股票数据 SDK）的 Node.js 运行时 |

### 1.4 系统上下文边界

HpAgent 核心进程（`main.py` 启动的 Temporal Worker）运行在 Docker 容器内，其外部世界由 13 个角色组成：

**上游入站（消息来源）**：
- **QQ 用户（群聊/私聊）**：人类用户，通过 QQ 客户端发送消息。以 @bot 触发对话，非 @ 消息仅收录群上下文。
- **QQ 服务端（腾讯）**：QQ 消息的底层传输通道。HpAgent 不直接与其通信，由 NapCat 代为收发。
- **NapCat QQ 客户端**：系统组件。通过 QQ 协议登录后，将 QQ 消息转换为 OneBot v11 JSON，通过 WebSocket 推送给 HpAgent 的 NapCatChannel（ws://hpagent:8082）。

**下游出站（服务消费）**：
- **Temporal Server**：系统组件。HpAgent 通过 gRPC 向其注册 Workflow 和 Activity 定义，接收信号和超时通知（端口 7233）。
- **Redis**：系统组件。HpAgent 通过 TCP 向其写入会话热数据、群上下文、PubSub 事件（端口 6379）。
- **Hindsight**：系统组件。HpAgent 通过 HTTP REST 向其提交记忆提取请求和检索查询（端口 8888）。
- **LLM 提供商（Mimo / MiniMax / 阿里百炼 / SiliconFlow）**：外部 SaaS。HpAgent 通过 HTTP REST 调用其模型 API（Chat Completion、Embedding、Rerank）。
- **MCP 工具服务（Tavily 搜索 / 股票 SDK / 高德地图 / 八字 / 菜谱 / 12306）**：外部系统。HpAgent 通过 MCP 协议（HTTP SSE 或 stdio）调用其工具能力。

**管理接口**：
- **Temporal Web UI**：运维人员通过浏览器查看工作流状态和历史（端口 8088）。
- **本地文件系统**：HpAgent 读写 `.data/` 目录下的会话归档、调度任务、日志文件。

HpAgent 不直接访问 PostgreSQL（Temporal 和 Hindsight 使用各自的 PostgreSQL 实例），不直接与 QQ 服务端通信。

---

## 2. 运行时架构（容器级）

### 2.1 容器拓扑

系统由 9 个容器（不含 profile）组成，分为 3 个逻辑层：

```
┌─────────────────────────────────────────────────────────────────┐
│                        应用层 (Application Layer)                 │
│                                                                  │
│  ┌──────────────────────┐    ┌─────────────────────────────────┐ │
│  │  hpagent              │    │  napcat-proxy  [proxy profile]  │ │
│  │  Python 3.11-slim     │    │  napcat-direct [direct profile]  │ │
│  │  + nsjail + Node.js   │    │  redsocks + iptables 透明代理     │ │
│  │  ws://0.0.0.0:8082    │    │  network_mode: host              │ │
│  └────────┬─────────────┘    └─────────────────────────────────┘ │
│           │                                                       │
└───────────┼───────────────────────────────────────────────────────┘
            │  Docker DNS
┌───────────┼───────────────────────────────────────────────────────┐
│                      中间件层 (Middleware Layer)                    │
│           │                                                       │
│  ┌────────┴──────┐  ┌──────────────────┐  ┌───────────────────┐  │
│  │  redis:7      │  │  temporal:1.26.2 │  │  hindsight:0.6.1  │  │
│  │  alpine       │  │  auto-setup      │  │  (ghcr.io)        │  │
│  │  6379         │  │  7233 (gRPC)     │  │  8888 (REST)      │  │
│  └───────────────┘  └───────┬──────────┘  └────────┬──────────┘  │
│                              │                       │            │
└──────────────────────────────┼───────────────────────┼────────────┘
                               │                       │
┌──────────────────────────────┼───────────────────────┼────────────┐
│                      数据层 (Data Layer)                          │
│                               │                       │            │
│  ┌────────────────────────────┴──┐  ┌─────────────────┴────────┐  │
│  │  temporal-postgres:16-alpine │  │  hindsight-postgres:     │  │
│  │  DB: temporal                │  │  pgvector/pgvector:pg16  │  │
│  │  (工作流状态持久化)             │  │  DB: hindsight (向量记忆)  │  │
│  └───────────────────────────────┘  └──────────────────────────┘  │
│                                                                   │
│  ┌───────────────────────────────┐                                 │
│  │  temporal-web:2.34.0 (8088)  │  ← 运维 Web UI                  │
│  └───────────────────────────────┘                                 │
└────────────────────────────────────────────────────────────────────┘
```

### 2.2 各容器单元详述

#### 2.2.1 hpagent（核心进程）

| 属性 | 说明 |
|------|------|
| **构建** | `src/Dockerfile`，基于 `python:3.11-slim`，安装 nsjail 3.4 静态二进制 + Node.js（stock-sdk MCP 所需） |
| **入口** | `entrypoint.sh` → `python -u -m main` |
| **职责** | 加载 YAML 配置 → 构建全量依赖图（ResourcePool、SessionStore、SandboxManager、HarnessRunner、ChannelRouter）→ 注册 Temporal Activity 和 Workflow → 启动消息监听 → 进入 asyncio 事件循环 |
| **协议** | 作为 WebSocket **服务端**监听 0.0.0.0:8082（NapCat 作为客户端连接）；作为 gRPC **客户端**连接 Temporal 7233；作为 HTTP **客户端**调用 Hindsight 和 LLM API；作为 TCP **客户端**连接 Redis |
| **依赖** | `depends_on: [redis, temporal, hindsight]`，均以 `condition: service_healthy` 严格等待 |
| **卷挂载** | `.data/` (rw, 运行时数据), `config/` (ro, 配置), `tools/` (rw, 工具定义 + ChromaDB) |
| **资源限制** | JSON file 日志 driver，单文件 50MB，最多 3 个文件 |

#### 2.2.2 redis

| 属性 | 说明 |
|------|------|
| **镜像** | `redis:7-alpine` |
| **职责** | 三用途：① 会话热数据缓存（键前缀 `hpagent:session:`，TTL 86400s）；② 群聊天上下文滑动窗口（每群 50 条消息上限）；③ PubSub 事件总线（通知临时事件） |
| **健康检查** | `redis-cli ping`，间隔 5s，超时 3s，重试 5 次 |

#### 2.2.3 temporal + temporal-postgres

| 属性 | 说明 |
|------|------|
| **temporal 镜像** | `temporalio/auto-setup:1.26.2`（含自动建库建表） |
| **temporal-postgres 镜像** | `postgres:16-alpine`，DB=temporal，用户=temporal |
| **职责** | Temporal 负责工作流编排：维护每个用户的长期运行工作流实例、接收信号（新消息/取消会话）、触发 Activity 执行、管理空闲超时。PostgreSQL 持久化工作流事件历史和执行状态 |
| **协议** | Temporal 对外暴露 gRPC（端口 7233），内部通过 PostgreSQL 协议连接 temporal-postgres |
| **健康检查** | `tctl cluster health`，间隔 10s，超时 5s，重试 12 次 |

#### 2.2.4 hindsight + hindsight-postgres

| 属性 | 说明 |
|------|------|
| **hindsight 镜像** | `ghcr.io/vectorize-io/hindsight:0.6.1` |
| **hindsight-postgres 镜像** | `pgvector/pgvector:pg16`（内存限制 512MB），DB=hindsight，用户=hindsight |
| **职责** | Hindsight 提供长期记忆：`retain`（接收对话事件 → LLM 事实提取 → pgvector 向量存储）、`recall`（接收查询 → pgvector 余弦相似度 + BM25 + 知识图谱混合检索 → 返回记忆项）、`reflect`（深度推理已有记忆，生成高级洞察） |
| **协议** | Hindsight 对外暴露 HTTP REST API（端口 8888，容器内映射为宿主机 8001）；内部通过 PostgreSQL 连接向量数据库 |
| **配置重点** | 使用外挂的 SiliconFlow Embedding（BGE-M3）和 Reranker，跳过 LLM 验证（`SKIP_LLM_VERIFICATION=true`）以加速启动 |
| **健康检查** | `curl -f http://localhost:8888/health`，间隔 10s，超时 5s，重试 30 次，start_period 30s |

#### 2.2.5 temporal-web

| 属性 | 说明 |
|------|------|
| **镜像** | `temporalio/ui:2.34.0` |
| **职责** | 运维管理界面。可视化工作流运行状态、事件历史、Activity 调用链。端口 8088 |

#### 2.2.6 napcat-proxy / napcat-direct（可选 profile）

| 属性 | napcat-proxy | napcat-direct |
|------|-------------|---------------|
| **触发** | `docker compose --profile proxy up` | `docker compose --profile direct up` |
| **构建** | 自定义 Dockerfile（基于 `mlikiowa/napcat-docker`），加装 redsocks + iptables | 直接使用 `mlikiowa/napcat-docker:latest` |
| **网络** | `network_mode: host`，需 `NET_ADMIN` capability | `network_mode: host`，UID=2000 避免被 iptables 拦截 |
| **职责** | 透明代理模式：`iptables REDIRECT(UID 1000) → redsocks → SOCKS5` 将 QQ 流量通过代理转发 | 直连模式：QQ 流量直接出站 |
| **代理降级** | 代理不可达时：`PROXY_OPTIONAL=true` 走直连；默认 fail-closed | 无代理 |
| **QQ 兼容** | 通过 wrapper 注入 `--disable-gpu --disable-gpu-sandbox` 参数禁用 GPU | 同左 |

### 2.3 通信拓扑

```
                    NapCat QQ 客户端
                    (host network)
                         │
                    WebSocket (客户端角色)
                         │
                         ▼
┌───────────────────────────────────────────────────────────┐
│  hpagent (app-network)                                    │
│                                                           │
│  NapCatChannel ←──────────────────→ ChannelRouter         │
│  ws://0.0.0.0:8082                                       │
│                                                           │
│  ─────────── 出站连接 ───────────                         │
│  → temporal:7233        gRPC (Workflow/Activity)         │
│  → redis:6379           TCP (会话/PubSub)                │
│  → hindsight:8888       HTTP REST (记忆)                 │
│  → 外部 LLM API          HTTPS (模型推理)                 │
│  → 外部 MCP 服务         HTTP/stdio (工具)               │
└───────────────────────────────────────────────────────────┘
```

所有容器间通信通过 Docker DNS 解析服务名，位于 `app-network` 桥接网络（NapCat 除外，使用 host 网络）。

---

## 3. 逻辑架构（组件级）

### 3.1 分层总览

HpAgent 的 Python 源码划分为 10 个包，按「手脑分离」原则分为 5 个逻辑层：

```
Layer 0: 入口层
  main.py                          ← 加载配置 → 启动 Worker

Layer 1: 指挥层 (Orchestration)
  orchestration/ (5 files)         ← Temporal 工作流 + 依赖注入 + 配置 + 调度

Layer 2: 大脑层 (Harness)
  harness/ (5 files)               ← Agentic Loop 全集：推理、工具调用、记忆

Layer 3: 双手层 (Sandbox)           ← 工具执行 + 消息渠道 + 安全沙箱
  sandbox/ (16 files)

Layer 4: 资源 & 持久化层
  resources/ (6) | session/ (5) | memory/ (3) | storage/ (3) | account/ (3)

Layer 5: 多 Agent 协作层
  agent/ (14 files)                ← Supervisor/Council/Workflow 策略

跨层共享:
  common/ (6 files)                ← 类型、接口、错误、日志
```

### 3.2 各组件详述

---

#### 3.2.1 入口层：`main.py`

**单一职责**：应用程序入口。加载 `.env` → 加载 YAML 配置（`config.yaml` + `models.yaml` + prompts + agents）→ 调用 `start_worker(config)` 进入主循环。

**对外接口**：无（模块自身）。它是系统的启动器，按 Python 包入口 `python -m main` 执行。

**依赖**：`orchestration.config`（加载配置）、`orchestration.worker`（启动 Worker）、`common.logging`（初始化日志）。

---

#### 3.2.2 指挥层：`orchestration/`

**`orchestration/config.py`** — 配置数据类树

| 属性 | 说明 |
|------|------|
| **职责** | 将项目全部 YAML 配置映射为强类型 dataclass 树（`AppConfig` 为根节点，含 10 个子 dataclass），提供 `from_yaml()` 类方法加载 |
| **对外接口** | `AppConfig(config_path, models_path)` → 返回完整配置对象，各属性的访问路径如 `config.models.fast[0].model`、`config.session.redis_ttl` |
| **核心抽象** | 所有 dataclass 均采用 `@dataclass` + `field(default=...)` 定义，缺失键使用默认值，扩展键记录 warning。环境变量覆盖通过 `_apply_env_overrides()` 方法在加载后按规则替换 |
| **依赖** | `pyyaml`、`dataclasses`、`os.path.expandvars`（解析 `${ENV_VAR}` 语法） |

**`orchestration/worker.py`** — 依赖注入中心

| 属性 | 说明 |
|------|------|
| **职责** | 系统的组装工厂。按顺序构建所有运行依赖：① 日志初始化 → ② CredentialManager + ResourcePool -> 模型降级链配置 → ③ SessionStore (Redis + Hindsight + WAL) → ④ ToolVectorStore + ToolRetriever (ChromaDB) → ⑤ MCP ToolManager → ⑥ ToolRegistry + SandboxManager → ⑦ 渠道注册 (NapCat/Official/Console) → ⑧ HarnessRunner 组装 → ⑨ Temporal Worker 启动 |
| **对外接口** | `start_worker(config)` → 异步函数，阻塞运行直到信号终止 |
| **核心抽象** | `_channel_factories: dict[ChannelType, Callable]` 渠道注册表；`handle_message(UnifiedMessage)` 消息回调闭包 |
| **依赖** | 几乎依赖所有其他包（harness、session、sandbox、resources、account、common），是本项目耦合度最高的文件 |

**`orchestration/workflow.py`** — Temporal 工作流定义

| 属性 | 说明 |
|------|------|
| **职责** | 定义 3 个 Temporal Workflow：`OrchestrationWorkflow`（主对话循环：等待消息信号 → 触发 Activity → 等待下一条消息或空闲超时）、`ReflectWorkflow`（定期记忆反思，由 Temporal Schedule 触发）、`MetricsReportWorkflow`（定期指标上报） |
| **对外接口** | `OrchestrationWorkflow.run(user_message)` → 工作流入口；Signal: `new_message(msg)` → 注入新消息；Signal: `cancel_session()` → 强制结束会话 |
| **核心抽象** | `WorkflowIDReusePolicy.ALLOW_DUPLICATE` → 保证一个用户一条工作流（id=`hpagent-{account_id}`）；`workflow.wait_condition()` + `pending_messages` 列表 → 信号驱动的消息队列模式；三阶段 drain 模式 → 处理迟到信号竞态 |
| **依赖** | `temporalio`、`harness.activities`、`datetime.timedelta` |

**`orchestration/scheduler.py`** — 定时任务调度器

| 属性 | 说明 |
|------|------|
| **职责** | 提供 `schedule(task_id, trigger_at/cron_expr, handler_id, params)` 和 `cancel(task_id)` 接口。通过后台 asyncio Task 轮询检查到期任务并触发 handler。任务数据持久化到 `.data/scheduler/scheduled_tasks.json` |
| **对外接口** | `TaskScheduler.data_dir` 属性；`schedule(...)` / `cancel(...)` / `list_by_filter(...)` |
| **核心抽象** | `handler_registry: dict[str, Callable]` — 按名称注册回调（当前仅注册 `user_reminder`）；`croniter` 库支持标准 cron 表达式 |
| **依赖** | `croniter`（惰性导入）、`asyncio` |

---

#### 3.2.3 大脑层：`harness/`

**`harness/runner.py`** — Agentic Loop 全集

| 属性 | 说明 |
|------|------|
| **职责** | 作为「大脑」，协调单次对话回合的完整流程：HyDE 查询改写 → 记忆召回 → 系统提示词/记忆/群上下文拼接 → LLM 推理 → 工具执行循环 → 回复发送 → 记忆留存。它是纯协调器，不持有持久化状态 |
| **对外接口** | `HarnessRunner.process_turn(session_id, user_message, event_history)` → 返回 LLM 响应文本 |
| **核心抽象** | `_rewrite_recall_query()` — HyDE 改写（将用户问题转换为声明式语句以提升向量检索命中率）；`_get_tools()` → `Sandbox.select_tools()` — 工具 RAG 选择；`_execute_tool()` → `Sandbox.execute()` — 工具执行路由；`_send_response()` → `ChannelRouter.send()` — 回复路由；`archive_session()`、`reflect()` — 生命周期管理 |
| **依赖** | `SessionStore`、`SandboxManager`、`ChannelRouter`、`ResourcePool`、`HarnessContextBuilder`、`MultiAgentExecutor`（条件分支，多 Agent 模式） |

**`harness/activities.py`** — Temporal Activity 薄封装

| 属性 | 说明 |
|------|------|
| **职责** | 5 个 `@activity.defn` 异步函数：`process_turn_activity`、`archive_session_activity`、`reflect_activity`、`reflect_batch_activity`、`metrics_report_activity`。每个函数仅做参数转发到 `_harness`（通过 `inject()` 设置的模块级 HarnessRunner） |
| **核心抽象** | 模块级单例注入：`inject(harness: HarnessRunner)` → 设置 `_harness`；Temporal Activity 通过装饰器注册，支持超时和重试策略 |
| **依赖** | `temporalio`、`harness/runner.py` |

**`harness/context_builder.py`** — 上下文构建器

| 属性 | 说明 |
|------|------|
| **职责** | 将事件历史（`EventRecord` 列表）转换为 LLM `messages` 列表。注入：① 渠道对应的机器人身份（从 `identities.yaml` 加载）→ ② 系统提示词 → ③ 渠道专属行为准则（`tool_enforcement_napcat` 等）→ ④ 记忆文本（`# 相关记忆` 段）→ ⑤ 群上下文文本（如有）→ ⑥ 历史轮次（含工具调用） |
| **对外接口** | `HarnessContextBuilder.build(events, memories_text, group_context_text)` → `list[dict]` 标准 messages 格式 |
| **依赖** | `PromptLoader`、`common.types` |

**`harness/prompts.py`** — 提示词加载器

| 属性 | 说明 |
|------|------|
| **职责** | 将 `PromptsConfig` dataclass 中的提示词字典包装为带 fallback 的访问器：`get_identity(channel_type)` → 渠道对应身份文本，未匹配时回退到 `DEFAULT_IDENTITY` |
| **对外接口** | `get_identity(channel)` / `get_guidance(channel)` / `get_system(key)` / `get_environment(key)` |
| **依赖** | `orchestration/config.py` 的 `PromptsConfig` |

---

#### 3.2.4 双手层：`sandbox/`

**`sandbox/sandbox.py`** — 工具选择 + 执行引擎

| 属性 | 说明 |
|------|------|
| **职责** | `select_tools(query, top_k)` — 工具选择管线：消费 hints 队列 → 多查询 RAG 检索 → 合并 required 工具 → 按相关性得分升序排列 → 返回 Top-K OpenAI function calling 格式；`execute(tool_name, arguments)` — 工具执行管线：提取 `next_tool_hint` 内部字段 → 按类别路由（native → nsjail / native 进程内；mcp → MCP 远端；skills → 技能引擎展开）→ 输出截断（50K 字符阈值）→ 返回 ToolResult + 审计信息 |
| **对外接口** | `Sandbox(workspace_path, tool_registry, nsjail_executor)` — 构造时将 workspace、工具注册表、nsjail 执行器绑定 |
| **核心抽象** | `_hints: list[str]` — 跨轮次工具检索偏好，由 LLM 通过 `next_tool_hint` 注入，`select_tools` 时消费并清空；`_drain_hints()` — 原子消费 hints |
| **依赖** | `ToolRegistry`、`ToolResult`（内部类型） |

**`sandbox/sandbox_manager.py`** — 沙箱池管理

| 属性 | 说明 |
|------|------|
| **职责** | 按 session_id 管理 Sandbox 实例的生命周期：`get_or_create(session_id, workspace_path)` — 按需创建，已存在则续期 → `destroy(session_id)` — 销毁 → `cleanup_idle(idle_seconds)` — 清理空闲沙箱 |
| **对外接口** | `SandboxManager(tool_registry, nsjail_config=None, ...)` — 构造时注入 ToolRegistry 和可选的 nsjail 配置 |
| **核心抽象** | `_sandboxes: dict[str, Sandbox]` — 会话-沙箱映射；`threading.RLock` — 保护并发访问；每个 Sandbox 创建时绑定唯一的 `sandbox_id`（UUID） |
| **依赖** | `Sandbox`、`NsjailConfig`、`NsjailExecutor`、`ToolRegistry` |

**`sandbox/nsjail.py`** — OS 级沙箱

| 属性 | 说明 |
|------|------|
| **职责** | 将 Bash 命令封装为 `nsjail` 子进程执行。限制：30 秒时间限制、256MB 内存、10 秒 CPU、最大 32 进程、64 文件描述符、`/proc` 禁用、网络禁用、只读根文件系统、以 `nobody:nogroup` 运行 |
| **对外接口** | `NsjailExecutor(NsjailConfig).execute(tool_name, arguments)` → `ToolResult` |
| **核心抽象** | `NsjailConfig` — 沙箱参数 dataclass，默认从 `SandboxConfig` dataclass 构造 |

---

#### 3.2.5 渠道层：`sandbox/channels/`

**`BaseChannel`** — 渠道抽象基类

| 属性 | 说明 |
|------|------|
| **职责** | 定义渠道统一接口：`normalize_message(raw) → UnifiedMessage`（入站标准化）、`send_message(UnifiedMessage) → bool`（出站发送）、`start_monitor(callback) → bool`（启动监听）、`stop_monitor() → bool`（停止监听） |
| **对外接口** | `IChannel` 抽象接口的默认实现骨架。子类实现 `normalize_message` 和 `send_message` |
| **核心模式** | 模板方法模式 — `start_monitor`/`stop_monitor` 提供默认实现（设置 `_monitoring` 标志和 `_callback` 引用），子类覆写以启动各自的传输层 |

**三个具体渠道实现**：

| 渠道 | 协议 | 入站方式 | 激活条件 |
|------|------|---------|----------|
| `NapCatChannel` | WebSocket 服务端 | OneBot v11 JSON 解析 → UnifiedMessage | `channels.enabled` 含 `napcat`，NapCat 客户端主动连接 |
| `OfficialQQChannel` | QQ Bot API v2 (HTTP + WebSocket) | QQ 官方消息格式 → UnifiedMessage | `channels.enabled` 含 `official_qq`，且环境变量 `QQ_OFFICIAL_APP_ID` + `QQ_OFFICIAL_CLIENT_SECRET` 已设置 |
| `ConsoleChannel` | stdin/stdout | 行文本 → UnifiedMessage | `channels.enabled` 含 `console` |

**`ChannelRouter`** — 渠道路由器

| 属性 | 说明 |
|------|------|
| **职责** | 维护 `ChannelType` → `IChannel` 的映射表，`send(UnifiedMessage)` 根据 `message.channel_type` 路由到对应渠道的 `send_message()` |
| **对外接口** | `register(channel_type, channel)` → 注册；`send(message)` → 路由发送 |
| **核心抽象** | `_channels: dict[ChannelType, IChannel]` — 路由表 |

---

#### 3.2.6 工具系统：`sandbox/tools/`

**`ToolRegistry`** — 三槽位工具注册表

| 属性 | 说明 |
|------|------|
| **职责** | 管理三槽位工具体系：`_native_tools`（本地工具，如 bash、fs_read/write/edit、glob_、grep、reminder）、`_mcp_tools`（MCP 远端工具）、`_skills`（技能工作流）。提供 `retrieve_for_llm_multi(queries, top_k)` 的 RAG 多查询检索 |
| **对外接口** | `register_native(tool)` / `register_mcp(tool)` / `register_skill(tool)` — 注册；`list_for_llm()` — 全量列出（OpenAI function calling 格式）；`retrieve_for_llm_multi(queries, top_k, max_merged)` — RAG 检索；`execute(tool_name, args)` — 执行 |
| **核心抽象** | `freeze()` → 禁止后续注册（启动后对不可信来源的防御）；`_extract_tool_name(tool_dict)` — 统一提取名称 |

**`ToolVectorStore` + `ToolRetriever`** — 工具 RAG

| 属性 | 说明 |
|------|------|
| **职责** | `ToolVectorStore` — 基于 ChromaDB 的持久化工具向量库（存储 tool_name → description → embedding 映射）；`ToolRetriever` — 语义检索管线：用户查询 → SiliconFlow BGE-M3 embedding → ChromaDB 余弦相似度 → 候选列表 → 可选 SiliconFlow BGE Reranker 重排序 → 返回 Top-K |
| **对外接口** | `ToolRetriever.retrieve(query, top_k)` → `list[(tool_name, score)]` |
| **核心抽象** | `last_scores: dict[str, float]` — 最近一次检索的相关性得分，供 Sandbox 按升序排列工具定义 |

**MCP 适配器**

| 属性 | 说明 |
|------|------|
| **职责** | `MCPToolManager` 通过 MCP 协议（HTTP SSE 或 stdio）与外部工具服务器建立连接，将其工具集注册到 `ToolRegistry` 的 mcp 槽位 |
| **依赖** | `config.models.yaml` 的 `mcp.servers` 配置段指定服务器列表 |

**Skills 引擎**

| 属性 | 说明 |
|------|------|
| **职责** | 解析 SKILL.md 格式的技能定义文件 → 实例化为可执行的工具对象 → 注册到 `ToolRegistry` 的 skills 槽位。技能是一个多步骤的工作流定义（类似管道），执行时按步骤展开 |

---

#### 3.2.7 资源层：`resources/`

**`ResourcePool`** — 模型资源池

| 属性 | 说明 |
|------|------|
| **职责** | 实现 `IResources` 接口。管理多个 `ModelClient` 实例，按类别（`chat`/`fast`/`embedding`/`image`/`reasoning`）组织降级链。`generate(messages, model_selector, tools)` 按降级链顺序尝试模型，捕获 `ModelAPIError`/`ConnectionError`/`TimeoutError` 后自动切换到下一个 |
| **对外接口** | `initialize_models()` → 从 CredentialManager 加载端点；`configure_fallback_group(group_name, model_ids)` → 配置降级链；`generate(messages, model_selector, tools, stream)` → 调用模型 |
| **核心抽象** | `_fallback_groups: dict[str, list[ModelEntry]]` — 降级链映射；延迟预算机制：慢速模型通过 `asyncio.wait_for` 提前取消 |

**`ModelClient`** — 单模型 HTTP 客户端

| 属性 | 说明 |
|------|------|
| **职责** | 封装 HTTP POST 调用单一模型 API。支持 Anthropic 和 OpenAI 两种 API 格式的自动适配：输入 `messages` 统一格式 → `_convert_messages()` 转换为目标格式 → 发送 POST → 解析响应内容 + 工具调用 |
| **对外接口** | `ModelClient.generate(messages, tools)` → `ModelResponse` |
| **核心抽象** | `_tools_to_anthropic()` / `_tools_to_openai()` — 工具定义格式转换；XML `<tool_call>` 兜底解析 — 处理不遵循标准 OpenAI function calling 但仍输出工具调用的模型 |

**`CredentialManager`** — 凭证管理器

| 属性 | 说明 |
|------|------|
| **职责** | 管理 API 密钥的生命周期存储：注册端点 → 加密存储密钥 → 返回脱敏列表。线程安全（`threading.RLock`） |
| **核心抽象** | `resource_id = f"model_endpoint:{index}:{provider}"` 作为密钥检索键；加密目前为占位实现（直通），设计上预留了 KMS 替换接口 |

**`EmbeddingClient` + `RerankerClient`**

| 属性 | 说明 |
|------|------|
| **职责** | 封装 SiliconFlow 的 BGE-M3 Embedding API 和 BGE Reranker API，提供 `embed(texts) → list[list[float]]` 和 `rerank(query, documents, top_n) → list[(doc, score)]` 接口 |

---

#### 3.2.8 持久化层：`session/` `memory/` `storage/`

**`SessionStore`** — 会话三层持久化

| 属性 | 说明 |
|------|------|
| **职责** | 三层持久化策略：① Redis 热缓存（键 `hpagent:session:{id}`，TTL 86400s，存储最近 N 轮事件） → ② WAL JSONL 文件（`.data/active-sessions/{id}.jsonl`，追加写入，故障恢复用） → ③ 永久归档 `.data/workspace/{account}/sessions/{id}/history.jsonl` + `meta.yaml`（LLM 摘要 + 统计） |
| **对外接口** | `SessionStore.recall_memories(account_id, query, tags)` → `(MemoryItem[], formatted_text)`；`SessionStore.retain_memories(account_id, events, context, tags)` → 异步提交；`SessionStore.archive(account_id, session_id)` → 归档 |

**`HindsightClient`** — 记忆服务客户端

| 属性 | 说明 |
|------|------|
| **职责** | 封装 Hindsight v0.6.1 REST API。`retain(text, context, tags)` → POST `/v1/default/banks/{bank_id}/memories`（服务端 LLM 提取事实 + BGE-M3 向量化 + pgvector 存储）；`recall(query, tags_filter)` → POST `/v1/default/banks/{bank_id}/memories/recall`（混合检索）。bank_id = `hpagent-u-{account_id}` 实现按用户隔离 |
| **核心抽象** | 降级策略：超时 → 立即降级；429 → 指数退避（最多 2 次）；5xx → 带重试降级；4xx → 不重试。`metrics` 对象跟踪成功/失败计数和 P50/P99 延迟 |

**`GroupContextStore`** — 群聊天上下文

| 属性 | 说明 |
|------|------|
| **职责** | 维护群聊短期上下文滑动窗口（Redis List，每群上限 50 条消息，由 `RedisConfig.group_context.max_size` 配置）。非 @bot 消息写入上下文但不全量触发回复；@bot 消息触发 Agentic Loop 并将上下文注入 LLM |

**`WorkspaceDB` + 文件存储**

| 属性 | 说明 |
|------|------|
| **WorkspaceDB** | SQLite 数据库，管理 workspace 元数据（账户目录结构、会话列表）。路径由 `WorkspaceConfig.db_path` 指定 |
| **LocalFileStore** | 实现 `FileStore` 协议，提供基础的 JSONL 文件读写能力（用于会话归档和调度任务持久化） |
| **RedisCache** | Redis 连接封装，含 `RedisPubSub` 子类（发布/订阅事件总线），Redis 不可用时自动降级为 `InMemoryPubSub` |

---

#### 3.2.9 多 Agent 协作层：`agent/`

| 组件 | 类 | 职责 |
|------|-----|------|
| 抽象接口 | `BaseAgent`, `ControlStrategy`, `AgentRegistry`, `MessageBus` | 定义多 Agent 系统的通用契约 |
| 控制策略 | `SupervisorControlStrategy` | LLM 动态规划 → 分发子任务 → 审查结果 → 迭代 |
| | `CouncilControlStrategy` | N 个 Agent 并行执行 → 裁决者判定最优结果 |
| | `WorkflowControlStrategy` | 静态 DAG 执行，适用于预定义流程 |
| 编排引擎 | `Orchestrator` | 接收 `ExecutionPlan` → 按策略调度 → 收集 `TaskResult` |
| 工厂 | `build_supervisor()` / `build_council()` / `build_workflow()` | 一行构建已配置的 Orchestrator |
| 执行器 | `MultiAgentExecutor` | 桥接 HarnessRunner 和多 Agent 系统 |
| 通信 | `InMemoryMessageBus` | Agent 间消息传递（内存实现） |
| 补偿 | `CompensationRegistry` | 任务失败时的补偿/回滚处理器注册表 |
| 适配 | `ReActAgent` | 将 HarnessRunner 包装为 `BaseAgent` 接口 |

**当前状态**：`agent/` 包在单 Agent（QQ 渠道）模式下仅通过 `HarnessRunner` 中的条件分支引用 `MultiAgentExecutor`。完整的多 Agent 工作流将在 Web 渠道接入后启用。

---

#### 3.2.10 跨层共享：`common/`

| 模块 | 提供 | 消费者 |
|------|------|--------|
| `common/types.py` | `UnifiedMessage`, `Event`, `ChannelType`, `ToolCall`, `ToolResult`, `ModelResponse` | 全系统所有包 |
| `common/interfaces.py` | `IResources(ABC)`, `IChannel(ABC)` | `ResourcePool`、`BaseChannel` |
| `common/errors.py` | `AgentError` 基类 + `ErrorCode` 枚举 + 6 个预定义子类 | 全系统 |
| `common/logging.py` | 双槽日志（彩色控制台 + JSONL 文件） | `main.py`、`worker.py` |
| `common/token_counter.py` | 基于字符数的 Token 估算 | `context_builder.py` |

---

## 4. 设计模式地图

### 4.1 策略模式（Strategy）

| 位置 | 参与类 | 解决的问题 |
|------|--------|-----------|
| `agent/strategies.py` | `ControlStrategy(ABC)`, `SupervisorControlStrategy`, `CouncilControlStrategy`, `WorkflowControlStrategy` | 多 Agent 编排策略的运行时切换：用户可选择 LLM 动态规划（Supervisor）、并行执行+裁决（Council）、还是预定义 DAG（Workflow），无需修改 Orchestrator 代码 |
| `agent/strategies.py` | `ResultAggregator`, concat/merge/first/last 策略 | 多 Agent 结果聚合方式的可配置化 |
| `resources/resource_pool.py` | `ResourcePool` 降级链 | LLM 模型选择的策略切换：不同 provider 或不同模型之间的自动降级，运行时可配置 |

### 4.2 注册表模式（Registry）

| 位置 | 核心类 | 解决的问题 |
|------|--------|-----------|
| `sandbox/tools/registry.py` | `ToolRegistry` | 三槽位工具注册（native/MCP/skills），支持 `freeze()` 锁死防止注册后修改，支持按 category 查询和全量列出 |
| `sandbox/channels/router.py` | `ChannelRouter` | `ChannelType` → `IChannel` 映射，根据消息类型自动路由发送，新增渠道只需注册无需修改 Router |
| `agent/registry.py` | `InMemoryAgentRegistry` | 按能力标签、优先级和并发槽位选择最优 Agent（支持 `select_best(capability_tags, context)` 查询） |
| `agent/compensation.py` | `CompensationRegistry` | 任务失败补偿：按 `task_type` 注册回滚处理器 |

### 4.3 工厂模式（Factory）

| 位置 | 核心函数/类 | 解决的问题 |
|------|------------|-----------|
| `sandbox/sandbox_manager.py` | `SandboxManager.create_sandbox()` | 按 session_id 创建完整装配的 Sandbox（含 ToolRegistry + NsjailExecutor + workspace 绑定） |
| `agent/factory.py` | `build_supervisor()`, `build_council()`, `build_workflow()` | 一行 API 构建已配置的 Orchestrator + ControlStrategy + MessageBus 组合 |
| `agent/factory.py` | `ResourcePoolAdapter` | 将 `ResourcePool.generate()` 适配为 `CallLLM(str) -> str` 协议，注入 LLMPlanner/LLMReviewer/LLMJudge |

### 4.4 适配器模式（Adapter）

| 位置 | 参与类 | 解决的问题 |
|------|--------|-----------|
| `sandbox/tools/adapters/mcp.py` | `MCPToolManager` | 将 MCP 协议的工具服务器适配为 ToolRegistry 的标准工具接口，隐藏 MCP 通信细节 |
| `agent/adapters.py` | `ReActAgent` | 将 HarnessRunner（单 Agent ReAct 模式）适配为多 Agent 系统中的 `BaseAgent` 接口 |
| `agent/factory.py` | `ResourcePoolAdapter` | 将 ResourcePool 的强类型 generate() 适配为多 Agent 系统期望的简化 `CallLLM` 协议 |
| `resources/model_client.py` | `ModelClient._convert_messages()`, `_tools_to_openai()`, `_tools_to_anthropic()` | 将 HpAgent 内部统一的 message 格式适配为不同 LLM API 格式（Anthropic vs OpenAI） |

### 4.5 模板方法模式（Template Method）

| 位置 | 参与类 | 解决的问题 |
|------|--------|-----------|
| `sandbox/channels/base.py` | `BaseChannel(IChannel)` | 定义消息渠道的骨架：`normalize_message()` + `send_message()` 为抽象方法（子类必须实现），`start_monitor()` / `stop_monitor()` 提供默认实现（子类可选覆写）。所有渠道遵循统一的生命周期 |

### 4.6 命令模式 / Activity 模式（Command）

| 位置 | 参与函数 | 解决的问题 |
|------|---------|-----------|
| `harness/activities.py` | `process_turn_activity`, `archive_session_activity`, `reflect_activity`, `reflect_batch_activity`, `metrics_report_activity` | 将 HarnessRunner 的业务方法封装为 Temporal Activity，使其获得 Temporal 提供的幂等性、超时、重试和分布式调度能力 |

### 4.7 责任链模式（Chain of Responsibility）

| 位置 | 参与类 | 解决的问题 |
|------|--------|-----------|
| `resources/resource_pool.py` | `ResourcePool.generate()` | LLM 调用降级链：主模型失败 → 自动尝试降级链中的下一个模型。每个模型可独立失败而不中断整体流程，仅在所有模型均失败时向上抛出异常 |
| `memory/hindsight_client.py` | `HindsightClient.recall()` / `HindsightClient.retain()` | 记忆服务降级链：超时 → 立即降级；429 → 指数退避；5xx → 带重试降级；4xx → 不重试。每种错误有独立的降级策略 |

### 4.8 观察者模式 / 发布-订阅（Observer / PubSub）

| 位置 | 参与类 | 解决的问题 |
|------|--------|-----------|
| `storage/redis.py` | `RedisPubSub` | 基于 Redis 的异步事件总线：每个 topic 一个 asyncio Task 监听者，handler 数量降为 0 时自动取消监听任务。Redis 不可用时回退为内存实现 |
| `orchestration/workflow.py` | `workflow.wait_condition()` + Signal | Temporal 信号驱动的事件通知：外部消息到达 → 触发 Signal → 工作流从等待中醒来处理 |

### 4.9 依赖注入模式（Dependency Injection）

| 位置 | 参与机制 | 解决的问题 |
|------|---------|-----------|
| `orchestration/worker.py` | `start_worker()` 函数全量组装 | 所有组件通过构造函数/工厂接收依赖，不存在全局单例（除 Activity 薄封装层出于 Temporal 限制的模块级注入）。HarnessRunner 接收 SessionStore、SandboxManager、ChannelRouter、ResourcePool 全部作为构造参数 |
| `harness/activities.py` | `inject(harness)` 模块级函数 | 绕过 Temporal Activity 不能使用类实例的限制，通过模块级变量注入 HarnessRunner |

---

## 5. 关键业务流程与数据流

### 5.1 流程一：QQ 群聊 @bot 对话回合（核心路径）

这是系统最高频的数据流，每一次 QQ 用户 @bot 触发一次完整的 Agentic Loop。

```
参与者: QQ 用户 → QQ 服务器 → NapCat → NapCatChannel → Worker.handle_message()
         → Temporal OrchestrationWorkflow → process_turn_activity()
         → HarnessRunner.process_turn() → ChannelRouter.send() → NapCatChannel
         → NapCat → QQ 服务器 → QQ 用户

时序:

1. QQ 用户: 在群聊中发送 "@nono 帮我查一下明天深圳的天气"
2. QQ 服务器: 将消息推送到 QQ 客户端
3. NapCat: 接收 QQ 消息 → 封装为 OneBot v11 JSON:

   {
     "post_type": "message",
     "message_type": "group",
     "group_id": 123456,
     "sender": {"user_id": 111, "nickname": "小明"},
     "message": [{"type": "at", "data": {"qq": "bot_qq"}},
                 {"type": "text", "data": {"text": "查天气"}}]
   }

4. NapCat → NapCatChannel: 通过 WebSocket 发送 JSON 字符串
5. NapCatChannel.normalize_message(raw):
     解析 JSON → 提取 at 段 → 检测 @bot → 剥离 CQ 码 → 构造 UnifiedMessage

   UnifiedMessage(
     sender_id="111",
     content="查天气",
     channel_type=ChannelType.NAPCAT,
     metadata={
       "group_id": 123456,
       "sender_name": "小明",
       "is_at_bot": true
     }
   )

6. NapCatChannel._callback(message) → Worker.handle_message(message)
7. Worker.handle_message():
     a. AccountService.resolve(NAPCAT, "111") → account_id
     b. GroupContextStore.append(group_id, message)  # 写群上下文
     c. 是 @bot → 构造 user_message = {
          "account_id": "...",
          "content": "查天气",
          "channel_type": "napcat",
          "sender_name": "小明",
          "group_id": 123456
        }
     d. Temporal: 以 workflow_id="hpagent-{account_id}" 启动/信号 OrchestrationWorkflow

8. OrchestrationWorkflow.run():
     a. 调用 process_turn_activity(user_message)
        → HarnessRunner.process_turn()
     b. 进入等待: 下一条消息信号 或 空闲超时(5分钟)

9. HarnessRunner.process_turn():
     9a. HyDE 改写:
         原始查询 "查天气" → ResourcePool.generate(model="fast", hyde_rewrite prompt)
           → "用户希望查询明天深圳的天气情况"

     9b. 记忆召回:
         SessionStore.recall_memories(account_id, "用户希望查询明天深圳的天气情况",
                                      tags="group:123456,channel:napcat")
           → HindsightClient.recall()
             → POST /v1/default/banks/hpagent-u-{account_id}/memories/recall
             → Hindsight: 查询向量嵌入 → pgvector cosine + BM25 + 知识图谱
             → 返回 [{text: "用户小明之前查询过天气，偏好简洁格式", score: 0.92}, ...]
           → 格式化记忆文本 → "# 相关记忆\n- 用户小明之前查询过天气..."

     9c. 上下文构建:
         HarnessContextBuilder.build(events, memories_text, group_context_text)
           → messages = [
               {role: "system", content: "你是 nono，一个 QQ 群聊助手..." + 系统提示词 + 准则 + 记忆},
               ...历史事件转换后的 messages
             ]

     9d. 工具选择:
         Sandbox.select_tools(query="查天气", top_k=8)
           → ToolRegistry.retrieve_for_llm_multi(["查天气"], 8, 12)
             → ToolRetriever.retrieve("查天气", 12)
               → SiliconFlow BGE-M3 embedding → ChromaDB 向量搜索
               → 候选: [("amap_weather", 0.89), ("web_search", 0.72), ...]
               → SiliconFlow BGE Reranker 重排序
               → 返回 Top-8 OpenAI function calling 工具定义

     9e. LLM 推理:
         ResourcePool.generate(messages, model_selector="chat", tools=selected_tools)
           → 遍历降级链 [Mimo pro → ...]
           → ModelClient 发送 POST → 返回 ModelResponse(
               content="我需要查询天气...",
               tool_calls=[{name: "amap_weather", arguments: {city: "深圳"}}]
             )

     9f. 工具执行:
         Sandbox.execute("amap_weather", {city: "深圳"})
           → ToolRegistry.execute("amap_weather", {city: "深圳"})
             → MCPToolManager → 高德地图 MCP 服务器 → HTTP 请求
             → ToolResult(success=True, output="深圳明天: 晴, 25-32°C")
           → 输出截断检查(50K字符阈值，不触发)
           → 结果写回事件流 → 循环到 9e (LLM 继续)

     9g. 回复发送:
         HarnessRunner._send_response(response_content)
           → ChannelRouter.send(UnifiedMessage(
               content="深圳明天晴，25-32°C...",
               channel_type=NAPCAT,
               metadata={group_id: 123456, at_trigger: true}
             ))
           → NapCatChannel.send_message():
               构造 OneBot v11 send_msg JSON:
               {
                 "action": "send_group_msg",
                 "params": {
                   "group_id": 123456,
                   "message": "[CQ:at,qq=111] 深圳明天晴，25-32°C ..."
                 }
               }
               发送到所有连接的 NapCat WebSocket 客户端
               (速率限制: 2 秒间隔)

     9h. 记忆留存:
         SessionStore.retain_memories(account_id, all_events, context, tags)
           → HindsightClient.retain()
             → POST /v1/default/banks/hpagent-u-{account_id}/memories
             → Hindsight 异步: LLM 提取事实 → Embedding → pgvector 存储
           → 写入 MEMORY_RETAIN 审计事件

10. OrchestrationWorkflow: 更新最后活动时间 → 进入等待

11. [空转超时] 5 分钟无新消息:
    OrchestrationWorkflow 触发 idle_timeout 分支
      → archive_session_activity()
        → HarnessRunner.archive_session()
          → 全量事件 → history.jsonl + meta.yaml
          → 清理 WAL + Redis + 群上下文退订 + RAG 缓存清理
      → 工作流结束
```

**关键数据结构传递**：
- 入站: `OneBot v11 JSON` → `UnifiedMessage`（`sender_id`, `content`, `channel_type`, `metadata`）
- 记忆: `MemoryItem(text, source, score)` → 文本拼接
- LLM: `messages: List[Dict[role, content]]` + `tools: List[Dict[name, description, parameters]]`
- 工具: `ToolCall(name, arguments)` → `ToolResult(success, output, error, metadata)`
- 出站: `UnifiedMessage` → `OneBot v11 send_msg JSON`

---

### 5.2 流程二：系统启动与依赖组装

```
参与者: Docker Compose → hpagent 容器 → main.py → worker.py

时序:

1. Docker Compose:
     ├── 启动 temporal-postgres → 健康检查通过
     ├── 启动 temporal → 健康检查通过
     ├── 启动 hindsight-postgres → 健康检查通过
     ├── 启动 hindsight → 健康检查通过
     └── 启动 redis → 健康检查通过
     ── 全部就绪 → 启动 hpagent ──

2. main.py:
     a. load_dotenv() → 加载 .env 环境变量
     b. load_config("config/config.yaml", "config/models.yaml")
        → AppConfig.from_yaml()
          → 解析 config.yaml → _from_dict() 递归填充 AppConfig dataclass
          → 解析 models.yaml → ModelsConfig.from_yaml()
          → 解析 config/prompts/*.yaml → PromptsConfig.from_dir()
          → 解析 config/agents.yaml → AgentEntry.from_dict()
          → _apply_env_overrides() 应用环境变量
        → 返回 AppConfig

3. worker.py → start_worker(config):
     a. 初始化日志: LogManager(config)
     b. 初始化 CredentialManager + ResourcePool
        → 遍历 models.yaml 的 providers: 注册 ModelEndpoint
        → 遍历 models.yaml 的 chat/fast/embedding等: configure_fallback_group()
        → ResourcePool.initialize_models()
     c. 初始化存储: RedisCache → LocalFileStore
     d. 初始化 SessionStore(redis, hindsight_client, wal_dir)
     e. 初始化 WorkspaceDB(.data/workspace/db.sqlite)
     f. 初始化 ToolVectorStore + ToolRetriever (ChromaDB → tools/vectors/)
     g. 初始化 ToolRegistry → 注册本地工具 (bash/fs_read/write/edit/glob_/grep/reminder)
     h. [可选] 连接 MCP 服务器 → 注册 MCP 工具
     i. [可选] 加载 Skills → 注册 Skill 工具
     j. 初始化 SandboxManager(tool_registry, nsjail_config)
     k. 初始化渠道:
        → 遍历 config.channels.enabled
        → 按 ChannelType 查 _channel_factories 字典
        → 实例化渠道 → 设置 bot_name → 注册到 ChannelRouter
        → channel.start_monitor(handle_message)
     l. 初始化 TaskScheduler → 注册 handler("user_reminder", callback)
     m. 构建 HarnessRunner(session_store, sandbox_manager, channel_router,
                          resource_pool, context_builder, agent_config)
     n. 注入到 activities.inject(harness_runner)
     o. 连接 Temporal Server(host:port)
     p. 注册 Activity + Workflow → Worker.run()
     q. 启动后台任务: scheduler._poll_loop, sandbox_cleanup_loop
     r. 进入 asyncio 事件循环 → await Future() (永久运行)
```

---

### 5.3 流程三：多模型降级切换

```
参与者: HarnessRunner → ResourcePool → ModelClient(主) → ModelClient(备用1) → ...

时序:

1. HarnessRunner: ResourcePool.generate(messages, model_selector="chat", tools=[...])
2. ResourcePool: 查 _fallback_groups["chat"] = [MimoPro, ...]
3. 尝试 MimoPro:
     a. ModelClient.generate(messages, tools)
     b. POST {base_url}/chat/completions → 超时 60s → TimeoutError
     c. ResourcePool 捕获 TimeoutError → logger.warning("DEGRADATION: ...")
4. 尝试下一个条目（如果存在）:
     a. ModelClient2.generate(messages, tools)
     b. POST → 返回 500 → ModelAPIError
     c. ResourcePool 捕获 ModelAPIError → 继续下一个
5. ... 所有条目失败:
     a. ResourcePool 抛出最终 ModelAPIError("All models in fallback group 'chat' failed")
     b. HarnessRunner 捕获 → 触发生成兜底回复:
        "抱歉，我暂时无法处理这个消息，请稍后再试。"

6. [成功路径] 某条目返回 ModelResponse:
     a. 记录 [TIMING] 日志 (模型名 + 延迟)
     b. 返回 HarnessRunner
```

---

### 5.4 流程四：定时提醒触发

```
参与者: TaskScheduler → 本地 JSON → HarnessRunner → QQ 用户

时序:

1. [注册] Worker.handle_user_reminder_request():
     a. TaskScheduler.schedule(task_id, trigger_at=tomorrow_9am,
                               handler_id="user_reminder",
                               params={user_id: "111", group_id: 123456, message: "起床啦"})
     b. TaskScheduler 内部: _tasks[task_id] = {...}; _save() → .data/scheduler/scheduled_tasks.json

2. [后台轮询] TaskScheduler._poll_loop() 循环 (interval=5s):

3. [触发] 当前时间 >= trigger_at:
     a. TaskScheduler: 调用 handler_registry["user_reminder"](params)
     b. handler → HarnessRunner 构造提醒消息
     c. ChannelRouter.send(UnifiedMessage(
          content="⏰ 提醒: 起床啦",
          channel_type=NAPCAT,
          metadata={group_id: 123456, at_trigger: true}
        ))
     d. NapCatChannel.send_message() → 发送到 QQ 群
     e. TaskScheduler: 删除任务 (一次性) 或更新 next_run_at (周期性)
     f. _save() → 更新 scheduled_tasks.json

4. QQ 用户: 收到提醒消息
```

---

### 5.5 流程五：会话归档

```
参与者: OrchestrationWorkflow (空闲超时) → HarnessRunner → 文件系统 + LLM

时序:

1. 5 分钟无新消息 → OrchestrationWorkflow.idle_timeout 触发
2. 调用 archive_session_activity(session_id, account_id)
3. HarnessRunner.archive_session():
     a. SessionStore.archive() → 返回全量事件列表
     b. write_history_jsonl(events, ".data/workspace/{account}/sessions/{id}/history.jsonl")
        → 逐行写入 JSONL 文件
     c. delete_wal(".data/active-sessions/{id}.jsonl") → 删除临时 WAL
     d. generate_session_summary(events):
        → ResourcePool.generate(model="fast", summary_prompt + events)
        → LLM 返回会话摘要文本
     e. write_meta_yaml(summary, tags, tool_stats)
        → 写入 ".data/workspace/{account}/sessions/{id}/meta.yaml"
     f. SessionStore.clear_redis(session_id) → 删除 Redis 缓存
     g. GroupContextStore.unsubscribe(group_id) → 退订群上下文
     h. SandboxManager.destroy(session_id) → 回收沙箱
     i. RAG 工具缓存清理
4. OrchestrationWorkflow: workflow.logger.info("Session archived: ...")
5. 工作流 Complete
```

---

## 6. 核心设计决策记录

### 决策 1：Temporal 作为编排引擎

| 维度 | 内容 |
|------|------|
| **背景与问题** | 需要一个引擎来管理「一个用户一条长期运行工作流」的会话模型。该模型需要支持：① 消息信号驱动的异步触发；② 空闲超时自动结束；③ 进程重启后恢复未完成的工作流（持久化）；④ 工作流级别的重试和错误处理 |
| **考虑的替代方案** | ① 纯 asyncio + Redis 状态机 —— 优势是实现简单，劣势是故障恢复需要自行实现状态持久化和重放；② Celery + 自定义状态管理 —— 优势是成熟的任务队列，劣势是需要大量胶水代码实现信号驱动和持久化状态机；③ Temporal —— 优势是原生支持 Workflow 持久化、信号、查询、调度、事件历史审计，劣势是引入额外的运维复杂度（需要独立的 Temporal Server + PostgreSQL） |
| **最终选择** | Temporal。决策关键点：Temporal 的 Signal + WorkflowIDReusePolicy 组合天然匹配「一个用户一条工作流」的模型，无需自行实现状态持久化和恢复逻辑；事件历史提供了天然的可审计性；定时 Schedule API 可用于定期记忆反思和指标上报；Activity 级别的超时和重试策略无需额外工具 |
| **正面影响** | 会话生命周期管理完全由基础设施保证；进程重启后工作流自动恢复；运维可通过 Temporal Web UI 查看所有工作流状态 |
| **负面影响** | 需要维护额外的 Temporal + PostgreSQL 基础设施；所有业务逻辑必须封装为 Activity 才能获得 Temporal 的保障，增加了薄封装层；调试工作流事件需要理解 Temporal 的事件模型 |

### 决策 2：Hindsight 作为长期记忆服务

| 维度 | 内容 |
|------|------|
| **背景与问题** | 需要一种持久化的记忆机制，使机器人在跨会话后仍能「记住」用户的偏好、需求和上下文。需求包括：① 语义检索（非关键词匹配）；② 自动事实提取（从对话中抽取结构化事实）；③ 按用户隔离；④ 按渠道/群聊过滤；⑤ 可配置的 LLM 提取策略 |
| **考虑的替代方案** | ① 自建 LangChain + ChromaDB + pgvector —— 优势是可控性强，劣势是需要自行实现事实提取、重排序、标签过滤、bank 隔离等全套逻辑；② 直接调用 LLM 做记忆总结 —— 优势是实现极简，劣势是随着记忆增长导致上下文爆炸且检索精度低；③ Hindsight —— 优势是开箱即用提供 retain/recall/reflect 三操作 + bank 隔离 + tags 过滤 + Reranker，劣势是 v0.6.1 不返回相似度评分，部分功能不够透明 |
| **最终选择** | Hindsight。决策关键点：开箱即用的「retain → LLM 提取 → embedding → 存储 → recall → 混合检索」管道，避免了自行开发记忆提取和检索逻辑；bank 隔离天然支持按用户拆分；tags 过滤支持 channel/group/scope 维度筛选 |
| **正面影响** | 记忆系统开发成本极低，API 调用即可；混合检索（向量 + BM25 + 知识图谱）的召回精度高于纯向量检索 |
| **负面影响** | 引入额外的 Hindsight + PostgreSQL 基础设施（共 2 容器）；v0.6.1 版本不返回检索置信度得分，需要自行按位置计算合成分数；`retain` 是服务端异步处理，会话完成到记忆可用之间存在延迟 |

### 决策 3：手脑分离架构

| 维度 | 内容 |
|------|------|
| **背景与问题** | Agentic 系统需要同时处理两个不同性质的职责：①「脑」—— 推理决策（调用 LLM、分析上下文、决定工具调用）；②「手」—— 工具执行（安全沙箱、输出截断、工具路由）。如果将两者耦合在一起，会导致每个工具执行细节的变更都需要修改核心推理逻辑，且难以复用 |
| **考虑的替代方案** | ① 单体循环（在单个 `Agent` 类中完成推理+工具调用）—— 优势是实现简单，劣势是测试困难、扩展性差；② 完整微服务拆分（推理服务和工具服务独立部署）—— 优势是独立扩展，劣势是延迟增加、复杂度爆炸；③ 手脑分离（同一进程内逻辑分层）—— 优势是职责清晰、每层可独立测试、新增工具不需修改推理层，劣势是多了一层抽象 |
| **最终选择** | 手脑分离（同一进程内逻辑分层）。决策关键点：在职责清晰和延迟之间取得了平衡。Harness 层负责决策循环（HyDE 改写 → 记忆召回 → 上下文构建 → LLM 推理 → 工具调用决策），Sandbox 层负责执行（工具选择 → 安全路由 → 执行 → 输出截断 → 审计信息生成）。两层通过 `Sandbox.select_tools()` 和 `Sandbox.execute()` 两个明确接口交互 |
| **正面影响** | 新增工具只需注册到 ToolRegistry 即可被 Sandbox 发现；Harness 层无需关心工具执行的安全细节；Sandbox 层可独立测试工具执行管线 |
| **负面影响** | 比单体循环多了一层抽象；`select_tools()` 返回的审计信息和 `execute()` 返回的审计信息分别由 Harness 和 Sandbox 管理，给事件日志的完整性带来协调成本 |

### 决策 4：多模型降级链

| 维度 | 内容 |
|------|------|
| **背景与问题** | 单一 LLM 提供商存在可用性风险：API 超时、限流、服务宕机等。需要一种机制在模型不可用时自动切换，而不是返回错误给用户。同时不同任务（chat / fast / embedding / reasoning）需要不同的模型选择策略 |
| **考虑的替代方案** | ① 不做降级，直接失败——劣势是用户体验差；② 通过反向代理（如 LiteLLM）统一降级——优势是不需要在代码中实现，劣势是引入额外的中间层和延迟；③ 在应用层按有序列表逐个尝试 |
| **最终选择** | 应用层按有序列表逐个尝试（`ResourcePool._fallback_groups`）。决策关键点：不引入额外服务依赖；降级逻辑对上层透明（`generate()` 接口不变）；分类别降级（chat 链和 fast 链可以有不同的备用模型） |
| **正面影响** | 高可用性：单个模型故障不中断服务；分类别降级：聊天模型用强大但慢的 MimoPro，快速任务（摘要/HyDE）用轻量但快的模型；延迟预算感知 |
| **负面影响** | 所有模型故障时总延迟 = 各模型超时之和（可能数十秒）；降级链需要在 Worker 启动时静态配置，不支持热更新 |

### 决策 5：asyncio 全异步模型

| 维度 | 内容 |
|------|------|
| **背景与问题** | 系统需要同时处理多个并发的 I/O 密集型操作：WebSocket 消息收发、HTTP API 调用（LLM + Hindsight）、Redis 读写、Temporal gRPC 通信。需要一个高效的并发模型来避免阻塞 |
| **考虑的替代方案** | ① 多线程 —— 简单但有 GIL 限制，且线程安全需要处处注意；② 多进程 —— 资源开销大，进程间通信复杂；③ asyncio 单线程协程 —— I/O 密集型场景下的理想选择 |
| **最终选择** | asyncio 单线程协程。决策关键点：所有 I/O 操作均为异步（httpx、websockets、redis[hiredis]、temporalio），无 CPU 密集型计算；单线程避免了竞态条件和死锁；代码风格统一 |
| **正面影响** | 无 GIL 竞争、无线程安全问题（仅 CredentialManager/ToolRegistry/SandboxManager 保留 RLock 用于防御性编程）；asyncio.Task 生命周期管理清晰 |
| **负面影响** | 无 CPU 密集型任务的卸载机制（无线程池/进程池）；长时间运行的同步操作会阻塞整个事件循环；沙箱清理循环的 `asyncio.create_task` 可能因大扫描而滞后 |

### 决策 6：NapCat 双模式代理架构

| 维度 | 内容 |
|------|------|
| **背景与问题** | QQ 机器人需要通过 QQ 的私有协议与服务器通信。NapCat 是容器化的 QQ 机器人桥接方案。国内环境下 QQ 服务器可能不可达，需要代理；也可能走直连。需要同时支持两种模式，且允许运行时切换 |
| **考虑的替代方案** | ① 单一代理模式 —— 不可切换；② Docker 网络层处理代理 —— 不够灵活 |
| **最终选择** | Docker Compose profile 机制实现双模式：`napcat-proxy`（iptables REDIRECT → redsocks → SOCKS5 透明代理）和 `napcat-direct`（直连，UID=2000 绕过 iptables）。通过 `docker compose --profile proxy/direct up` 选择 |
| **正面影响** | 两种模式互不干扰，通过 profile 隔离；透明代理（内核 NAT 层）比 proxychains（用户态 LD_PRELOAD hook）更快且更可靠（避免 QQ NT 内核 3 秒 OIDB 超时）；`PROXY_OPTIONAL=true` 支持代理不可用时降级为直连 |
| **负面影响** | 需要 `NET_ADMIN` capability（安全风险）；`network_mode: host` 与 Docker 网络隔离不兼容；iptables owner 模块在某些内核中不可用（已实现 proxychains 回退） |

### 决策 7：三层会话持久化策略

| 维度 | 内容 |
|------|------|
| **背景与问题** | 会话数据需要在不同生命周期阶段有不同访问模式：① 活跃会话需要快速读写（Redis）；② 进程重启后需要恢复未归档的会话（WAL）；③ 长期需要可审计的永久归档（JSONL + meta.yaml） |
| **考虑的替代方案** | ① 全 Redis 持久化 → 内存成本高，且无永久归档格式；② 全文件 → 缺少热数据快速访问能力；③ 三层策略 |
| **最终选择** | Redis（热数据 24h TTL）→ WAL JSONL（故障恢复，追加写入）→ history.jsonl + meta.yaml（永久归档，LLM 摘要） |
| **正面影响** | 每种访问模式都有优化存储；WAL 保证进程异常退出不丢失数据；归档格式便于离线分析 |
| **负面影响** | 三层需要维护数据一致性；归档流程是 Activity 调用，如果归档过程中崩溃可能导致部分数据不一致 |

---

## 7. 技术实现要点

### 7.1 并发模型

- **模型**：asyncio 单线程事件循环，所有 I/O 操作均为 `await` 异步调用
- **后台任务**：`asyncio.create_task()` 用于长期运行的后台协程（调度器轮询、沙箱清理），在 `worker.py` 中注册 `_background_tasks` 列表后通过 `task.cancel()` + `asyncio.gather()` 实现优雅关闭
- **Temporal 并发**：每个用户一条独立工作流实例，Temporal Server 负责在多 Worker 之间调度 Activity 执行
- **WebSocket 多客户端**：NapCatChannel 维护 `_connected_clients: set[WebSocketServerProtocol]` 集合，消息发送时广播到所有连接的 NapCat 客户端（通常是 1 个）
- **线程安全**：虽然主力是 asyncio 单线程，`CredentialManager`、`ToolRegistry`、`SandboxManager` 的写操作均使用 `threading.RLock` 保护，用于防御未来可能的线程使用场景

### 7.2 错误处理策略

系统定义了一套四级错误处理体系：

| 级别 | 场景 | 处理方式 |
|------|------|---------|
| **可恢复 → 重试** | `MODEL_API_ERROR`、`TOOL_EXECUTION_FAILED`、`NETWORK_ERROR` | 自动重试（Temporal Activity 级别重试）或降级链自动切换 |
| **可恢复 → 降级** | Hindsight 不可用 | 返回空记忆列表，不中断主流程 |
| **不可恢复 → 优雅兜底** | 所有工具轮次耗尽仍无回复、强制生成失败 | 返回硬编码兜底文本："抱歉，我暂时无法处理这个消息，请稍后再试。" |
| **不可恢复 → 终止** | `SESSION_NOT_FOUND`、`VALIDATION_ERROR` | 向上抛出，Temporal Activity 标记为失败，记录日志 |

所有异常继承自 `AgentError(ErrorCode, message, recoverable, details)`，`recoverable` 字段被 Temporal 的 Activity 重试策略引用。`ErrorCode` 枚举值提供程序化分类（非字符串匹配）。

### 7.3 日志规范

- **双槽设计**：`common/logging.py` 同时输出到彩色控制台（按级别着色）和 JSONL 文件（结构化日志）
- **命名规范**：logger 名称格式 `HpAgent.{Component}`（如 `HpAgent.Sandbox`、`HpAgent.ResourcePool`）
- **关键标签**：
  - `DEGRADATION:` — 降级事件（如模型切换、Hindsight 回退）
  - `[TIMING]` — 模型调用耗时
  - `logger.exception()` — 会话/工作流级别的异常（含完整 traceback）
- **字段约定**：日志 record 的 `extra` 字段传递 `account_id` 和 `session_id`（在 `worker.py` 的消息处理入口注入）

### 7.4 配置管理体系

- **来源**：① `config/config.yaml`（应用配置）→ ② `config/models.yaml`（模型/提供商/MCP/Skills 配置）→ ③ `config/prompts/`（identities.yaml, guidance.yaml, system.yaml）→ ④ `config/agents.yaml`（多 Agent 定义）→ ⑤ `.env` 中的环境变量
- **加载机制**：`AppConfig.from_yaml()` 类方法执行四步加载：
  1. 解析 config.yaml → `_from_dict()` 递归填充 `AppConfig` 及其子 dataclass
  2. 解析 models.yaml → `ModelsConfig.from_yaml()` 构建提供商和退避链
  3. 解析 prompts/ 目录 → `PromptsConfig.from_dir()` 构建提示词字典
  4. 解析 agents.yaml → `AgentEntry.from_dict()` 构建 Agent 定义
- **环境变量覆盖**：`_apply_env_overrides()` 在加载后按规则将环境变量映射到配置字段（如 `TEMPORAL_HOST` → `temporal.host`）；YAML 中的 `${ENV_VAR}` 通过 `os.path.expandvars()` 解析
- **验证策略**：使用 Python dataclass 默认值填缺失键；未知键记录 `logger.warning` 但不拒绝启动；API 密钥为空记录 warning（允许通过环境变量在运行时注入）

### 7.5 安全设计

- **nsjail 沙箱隔离**：仅对 Bash 工具启用（`nsjail_enabled: False` 默认），启用后以 `nobody:nogroup` 在只读根文件系统中执行，禁用网络和 `/proc`，内存限制 256MB，CPU 限制 10s，时间限制 30s
- **凭证管理**：API 密钥通过 `CredentialManager` 统一管理。存储时密钥被加密（当前为 pass-through 占位，预留 KMS 接口），列表返回时脱敏副本（`api_key=""`），调用时解密填充
- **输入保护**：
  - HyDE 查询改写上下文在每轮开始前清理（`_last_hyde_context = None`），防止跨轮次泄漏
  - 工具 `next_tool_hint` 字段在发送前剥离（不让工具实现看到 LLM 的提示信息）
  - XML `<tool_call>` 标签从最终回复中剥离，防止模型泄漏内部指令
  - 群聊中仅在有多个活跃订阅者时才发送 @mention，避免单用户场景下的冗余通知
  - 非 @bot 消息仅写入群上下文字段，不触发 Agentic Loop

### 7.6 上下文工程

关键参数均在 `AgentConfig` 中可配置：

| 参数 | 默认值 | 用途 |
|------|--------|------|
| `context_budget` | 256,000 tokens | LLM 上下文总预算 |
| `generation_headroom` | 16,000 tokens | 为 LLM 生成预留的空间 |
| `summary_budget` | 2,000 tokens | 会话摘要最大 token |
| `memory_budget` | 2,000 tokens | 记忆文本最大 token |
| `max_tool_rounds` | 20 | 单轮最大工具调用次数 |
| `max_history_turns` | 10 | 保留的历史对话轮数 |
| `compress_interval` | 8 | 每 N 轮触发一次历史压缩（0=禁用） |
| `checkpoint_interval` | 10 | 每 N 轮写入一次检查点（0=禁用） |
| `idle_timeout` | 300s | 会话无消息超时 |
| `inherit_context` | True | 是否继承上轮上下文（跨轮记忆连续性） |
| `wal_enabled` | True | 是否启用预写日志 |

---

## 8. 已知局限与演进方向

### 8.1 当前技术债务

**DEBT-01: 凭证加密为空实现**

- **位置**: `resources/credentials.py:172-176`
- **描述**: `_encrypt()` 和 `_decrypt()` 方法是 pass-through（返回原值），API 密钥以明文形式存储在内存中。代码注释明确标注了"生产环境应使用 KMS / 环境变量"
- **影响**: API 密钥在内存 dump 时可能泄露；无密钥轮转机制
- **建议**: 接入 AWS KMS / HashiCorp Vault / 环境变量注入。或至少使用 Python `secrets` 模块做内存加密

**DEBT-02: nsjail 默认禁用**

- **位置**: `config.yaml` 的 `sandbox.nsjail_enabled: False`
- **描述**: OS 级沙箱默认关闭，本地工具（bash/fs_read/fs_write 等）在进程内执行，仅依赖 Docker 容器级别隔离
- **影响**: 如果模型生成 `rm -rf /` 或 `cat /etc/passwd` 等恶意命令，没有 nsjail 防护时可能影响宿主机文件系统（虽然受限于 Docker 容器边界）
- **建议**: 在安全性要求高的场景默认启用

**DEBT-03: Hindsight 评分近似**

- **位置**: `memory/hindsight_client.py:105-106`
- **描述**: Hindsight v0.6.1 在 recall 返回值中不包含每个记忆项的相似度评分。当前采用基于列表位置计算合成相关性的方式：`(total - i) / total`
- **影响**: 所有记忆项的相关性得分是估算的，非真实语义相似度。低分项可能在后续排序中被错误地排在前面
- **建议**: 升级至支持评分返回的 Hindsight 版本，或调用单独的 Reranker 对召回结果重新评分

**DEBT-04: 无 Prometheus 指标暴露**

- **位置**: 全局
- **描述**: 系统缺乏结构化指标暴露端点。模型调用耗时仅通过 `[TIMING]` 日志标签记录，Hindsight 操作指标通过本地 `metrics` 字典跟踪，缺乏集中式的监控和告警能力
- **影响**: 生产运维依赖日志解析，无法对接 Prometheus/Grafana 等标准监控体系
- **建议**: 引入 `prometheus_client` 库，暴露 `/metrics` 端点，迁移关键指标（模型调用 P50/P99 延迟、降级次数、Hindsight 成功率、会话速率等）

**DEBT-05: 工具 RAG 缓存粒度**

- **位置**: `harness/runner.py` 工具选择逻辑
- **描述**: 工具 RAG 结果按 session_id 缓存，但未考虑用户查询语义变化对工具集的影响。缓存命中时直接返回上次结果，可能错过新工具
- **影响**: 同一会话内用户从「查天气」切换到「分析股票」时可能仍使用旧的工具集（如果缓存未失效）
- **建议**: 引入基于查询语义变化的自适应缓存失效策略

### 8.2 已预见但尚未实施的改进

**PLAN-01: Web 渠道 + 多 Agent 启用**

- 目标：通过 Web 界面接入用户，启用 `agent/` 包的多 Agent 协作能力（Supervisor/Council/Workflow 策略）。当前 `agent/` 包（14 文件）已完整实现，仅在 `HarnessRunner` 中以条件分支引入
- 需要：新增 `WebChannel` 实现（HTTP/SSE），扩展 `_channel_factories`，在 Web 请求路径中触发 `MultiAgentExecutor` 而非单 Agent 循环

**PLAN-02: 凭证加密升级**

- 见 DEBT-01

**PLAN-03: 异步工具执行**

- 当前 `Sandbox.execute()` 是同步等待工具结果（虽然工具内部可能是异步的）。计划支持并行工具调用（当 LLM 一次性返回多个 tool_call 时，并行执行以减少总延迟）

**PLAN-04: 会话导入/导出**

- 支持将会话历史导出为可分享的格式（如 HTML 对话记录），以及从外部导入对话数据进行记忆回填

**PLAN-05: 插件化记忆后端**

- 当前记忆后端硬编码为 Hindsight。计划抽象 `IMemoryBackend` 接口，支持切换到其他记忆服务（如 Mem0、自建 LangChain + pgvector 等），实现 `retain/recall/reflect` 三操作的多态

**PLAN-06: 端到端测试框架**

- 当前缺少自动化测试（无 `tests/` 目录）。计划引入：① 组件级单元测试（ResourcePool、ContextBuilder 等）；② 集成测试（Temporal 本地开发模式）；③ 渠道模拟器（模拟 NapCat WebSocket 消息流）

### 8.3 已知约束

- **单进程单 Worker**：当前部署架构是单个 `hpagent` 容器运行单个 Temporal Worker。Temporal 支持多 Worker 扩缩容，但需要验证 SessionStore 的 Redis 层在分布式场景下的竞态条件（WAL 追加写入的文件锁问题）
- **NapCat 单点**：QQ 渠道依赖单个 NapCat 实例，如果 NapCat 进程崩溃或 QQ 账号被风控，整个 QQ 渠道不可用。暂无多账号或多 NapCat 实例的热备方案
- **Temporal 版本锁定**：当前使用 `temporalio/auto-setup:1.26.2`，升级到 1.27+ 需要验证工作流兼容性（特别是 `WorkflowIDReusePolicy.ALLOW_DUPLICATE` 的语义）
- **ChromaDB 单文件锁**：ChromaDB 本地持久化客户端以单文件模式运行，多进程并发访问时会因文件锁冲突而失败。这意味着当前不能启动多个 Worker 实例共享同一个 `tools/vectors/` 目录
