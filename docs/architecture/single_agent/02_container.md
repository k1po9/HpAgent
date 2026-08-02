# 02 — 容器架构（单 Agent）

> 对应视觉文件：[`diagrams/02_container.excalidraw`](diagrams/02_container.excalidraw)

本文是单 Agent 运行时、部署单元及容器通信关系的设计事实源；Excalidraw 只做人工维护的视觉表达。

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
| **职责** | 加载 YAML 配置 → 构建全量依赖图（ResourcePool、SessionStore、SandboxManager、TurnOrchestrator、ChannelRouter）→ 注册 Temporal Activity 和 Workflow → 启动消息监听 → 进入 asyncio 事件循环 |
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

