# HpAgent —— 智能对话代理框架

基于 "手脑分离" (hand-brain separation) 架构的智能对话代理系统，Temporal Workflow 编排，nsjail OS 级沙箱隔离，Hindsight 长期记忆。

## 理念

在 QQ 端，它轻如问候，柔似提醒，是每日陪伴的挚友。

在 Web 端，它化身沉稳的管家，以更正式的对话承接你的重托：
- **委托** —— 跟进整个过程进展，而非仅仅交付答案
- **透明** —— 过往行为凝练为可回溯的总结，信任建立在可审计的事实之上
- **专属** —— 对你的理解具象为可预览、可掌控的 Skill 清单

## 架构：五层手脑分离

```
┌─ main.py ──────────────────────────────────────────────┐
│  加载配置 → 组装依赖 → 注册 Activity → 启动渠道监听      │
└────────────────────────────────────────────────────────┘

┌─ orchestration/  编排层（指挥）──────────────────────────┐
│  workflow.py     Temporal Workflow：agentic loop 确定性编排 │
│  worker.py       依赖初始化 + 渠道消息 → Workflow 启动/信号 │
│  config.py       AppConfig 强类型配置（dataclass 层次结构） │
└────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ harness/ 大脑 │ │ session/ 记忆 │ │ sandbox/ 双手 │
│              │ │              │ │              │
│ runner.py    │ │ store.py     │ │ nsjail.py    │
│ 无状态协调器  │ │ 事件流+召回  │ │ OS 级隔离执行 │
│              │ │              │ │              │
│ activities.py│ │ models.py    │ │ channels/    │
│ 5个Activity  │ │ 领域模型     │ │ 多渠道适配    │
│              │ │              │ │              │
│ prompts.py   │ │              │ │ tools/       │
│ 提示词加载器  │ │              │ │ 工具体系      │
└──────────────┘ └──────────────┘ └──────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ resources/   │ │ memory/      │ │ workspace/   │
│ 模型调用池   │ │ 长期记忆     │ │ 多用户工作区  │
│              │ │              │ │              │
│ resource_    │ │ hindsight_   │ │ manager.py   │
│ pool.py      │ │ client.py    │ │ 目录 + DB    │
│ 退避链调度   │ │ 向量检索     │ │              │
│              │ │              │ │ db.py        │
│ model_       │ │              │ │ SQLite 元数据│
│ client.py    │ │              │ │              │
│ HTTP 客户端  │ │              │ │ models.py    │
└──────────────┘ └──────────────┘ └──────────────┘
                          │
          ┌───────────────┼───────────────┐
          ▼               ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ storage/     │ │ account/     │ │ common/      │
│ 持久化抽象   │ │ 跨渠道账号   │ │ 公共基础设施  │
│              │ │              │ │              │
│ redis.py     │ │ account_     │ │ types.py     │
│ Redis 缓存+  │ │ service.py   │ │ 枚举+数据类  │
│ PubSub       │ │ QQ/Web →     │ │              │
│              │ │ account_id   │ │ interfaces.py│
│ protocols.py │ │              │ │ 核心接口 ABC │
│ Protocol 定义│ │ models.py    │ │              │
│              │ │              │ │ errors.py    │
│ _memory.py   │ │              │ │ 异常体系     │
│ 内存回退     │ │              │ │              │
└──────────────┘ └──────────────┘ └──────────────┘
```

## 项目结构

```
HpAgent/
├── docker-compose.yaml              # 完整服务栈编排
├── .env                             # 敏感配置（不会被 git 跟踪）
├── .gitignore
├── docs/                            # 设计文档
│   └── v8/                          #   v8 迭代：记忆模块重新设计
│       ├── hindsight-memory-best-practices.md   # Hindsight 最佳实践 + NapCat 数据流设计
│       └── napcat-api-reference.md              # NapCat API 参考（消息/群组/用户/频道）
├── config/
│   ├── config.yaml                  # 应用配置（Temporal / Redis / Sandbox / Hindsight）
│   ├── models.yaml                  # 模型提供商 + 降级链（API key 通过 ${ENV_VAR} 注入）
│   └── prompts/                     # LLM 提示词模板
│       ├── system.yaml              #   系统级角色定义
│       ├── guidance.yaml            #   行为指导
│       ├── identities.yaml          #   身份/人格模板
│       └── environment.yaml         #   运行环境描述
├── src/
│   ├── main.py                      # 入口：加载配置 → 启动 Worker（single/multi agent）
│   ├── entrypoint.sh                # Docker 容器启动脚本
│   ├── Dockerfile                   # 镜像构建（Python 3.11 + nsjail）
│   ├── requirements.txt             # Python 依赖
│   │
│   ├── orchestration/               # 编排层（指挥）
│   │   ├── config.py                #   AppConfig 强类型配置（dataclass）
│   │   ├── workflow.py              #   OrchestrationWorkflow：确定性编排核心
│   │   └── worker.py                #   依赖初始化 + Temporal Worker 启动 + 渠道监听
│   │
│   ├── harness/                     # 线束层（大脑）
│   │   ├── runner.py                #   HarnessRunner：无状态协调器（聚合所有依赖）
│   │   ├── activities.py            #   5 个 Temporal Activity：模型调用/工具执行/记忆更新
│   │   ├── context_builder.py       #   事件流 → LLM messages 上下文构建
│   │   └── prompts.py               #   PromptLoader：从 config/prompts/ 加载模板
│   │
│   ├── agent/                        # 多 Agent 协作层
│   │   ├── runner.py                  #   MultiAgentExecutor：多 Agent 编排执行器
│   │   ├── orchestrator.py            #   AgentOrchestrator：任务分解 + Agent 路由
│   │   ├── llm_agent.py               #   RealLLMPlanner：工具调用 Agent 实现
│   │   ├── composite.py               #   复合 Agent 组合模式
│   │   ├── strategies.py              #   协作策略（sequential/concurrent/review）
│   │   └── types.py                   #   Agent 相关类型定义
│   │
│   ├── session/                     # 会话层（记忆）
│   │   ├── store.py                 #   SessionStore：事件流 + Hindsight 召回 + JSONL 备份
│   │   ├── models.py                #   Session / SessionStatus / EventRecord 领域模型
│   │   └── repositories.py          #   持久化仓库
│   │
│   ├── sandbox/                     # 沙箱层（双手）
│   │   ├── nsjail.py                #   NsjailConfig + NsjailExecutor：OS 级隔离执行
│   │   ├── runner.py                #   沙箱内工具调度器（在 nsjail 命名空间内运行）
│   │   ├── sandbox.py               #   Sandbox：工具注册表 + 执行接口
│   │   ├── sandbox_manager.py       #   沙箱生命周期管理（创建/销毁/空闲回收）
│   │   ├── channels/                #   渠道适配
│   │   │   ├── base.py              #     BaseChannel 抽象
│   │   │   ├── napcat.py            #     NapCat QQ（OneBot v11 WebSocket）
│   │   │   ├── console.py           #     Console 开发渠道
│   │   │   └── router.py            #     ChannelRouter：渠道 → 统一消息路由
│   │   └── tools/                   #   工具体系
│   │       ├── base.py              #     BaseTool 抽象
│   │       ├── registry.py          #     ToolRegistry：工具注册表
│   │       └── factory.py           #     ToolFactory：默认工具集创建
│   │
│   ├── resources/                   # 资源层（模型调用）
│   │   ├── resource_pool.py         #   多模型注册 + 退避链调度
│   │   ├── model_client.py          #   单模型异步 HTTP 客户端
│   │   └── credentials.py           #   凭据管理 + 临时 token
│   │
│   ├── memory/                      # 长期记忆（Hindsight v0.6.1）
│   │   └── hindsight_client.py      #   Hindsight HTTP 客户端：retain/recall/reflect
│   │                                #   v8 增强：完整渠道上下文 → 多维度 tag/metadata/observation
│   │
│   ├── storage/                     # 存储抽象层
│   │   ├── protocols.py             #   Protocol 定义（KeyValueStore / FileStore / PubSub）
│   │   ├── redis.py                 #   RedisCache + RedisPubSub
│   │   ├── _memory.py               #   InMemoryKVStore / _NoopCache（开发回退）
│   │   ├── file.py                  #   AioFileStore：原子文件写入
│   │   ├── postgres.py              #   SqlKeyValueStore
│   │   └── container.py             #   InfraContainer：DI 装配
│   │
│   ├── workspace/                   # 多用户工作区
│   │   ├── manager.py               #   WorkspaceManager：目录骨架 + nsjail bind mount
│   │   ├── db.py                    #   WorkspaceDB：SQLite 元数据
│   │   └── models.py                #   User / Session / Artifact 模型
│   │
│   ├── account/                     # 跨渠道账号
│   │   ├── account_service.py       #   channel_type + sender_id → account_id
│   │   └── models.py                #   Account 模型
│   │
│   └── common/                      # 公共基础设施
│       ├── types.py                 #   枚举 + 数据类（UnifiedMessage / ToolResult / Event）
│       ├── interfaces.py            #   核心接口 ABC（IResources / ISandbox / IChannel / ITool）
│       └── errors.py                #   统一异常体系
│
└── test/
    └── test_hindsight.py            # Hindsight 集成测试
```

## 数据流：一条 QQ 消息的生命周期

```
NapCat QQ 客户端
    │  WebSocket (JSON)
    ▼
NapCatChannel.normalize_message()
    │  UnifiedMessage
    ▼
Worker.handle_message()
    │  account_service.resolve() → account_id
    │  session_id = "session-{account_id}"
    ▼
Temporal Workflow（启动或 Signal）
    │  OrchestrationWorkflow.run()
    ▼
HarnessRunner.process_turn()          ← 单次 agentic loop
    │
    ├─ 1. SessionStore 加载事件流     → 从 Redis / 文件恢复历史
    ├─ 2. SessionStore 召回长期记忆   → Hindsight 向量检索（渠道标签过滤）
    ├─ 3. ContextBuilder.build()      → 事件流 → OpenAI messages
    ├─ 4. ResourcePool.generate()     → 模型调用（退避链）
    │    └─ multi 模式: MultiAgentExecutor 多 Agent 协作
    ├─ 5. 如果有 tool_calls:
    │        SandboxManager → NsjailExecutor.execute()
    │        在 nsjail 命名空间内运行 runner.py
    │        结果写回事件流 → 回到步骤 4
    ├─ 6. SessionStore 提取长期记忆   → Hindsight retain（完整渠道上下文）
    └─ 7. ChannelRouter.send()        → NapCat → QQ 平台
```

## 关键设计决策

| 决策 | 说明 |
|------|------|
| **手脑分离** | 编排层只做决策，沙箱层执行操作。模型调用走 ResourcePool 退避链，工具调用走 nsjail 隔离 |
| **多 Agent 协作** | 支持 single/multi 模式。Multi-agent 模式由 MultiAgentExecutor 编排多个 Agent 协作完成任务 |
| **Temporal Workflow** | agentic loop 作为确定性 Workflow，支持故障恢复、Signal 中断、自动持久化 |
| **nsjail OS 级隔离** | 每次工具调用在独立 PID/NET/FS 命名空间中执行，Docker 内无需 DinD |
| **跨渠道统一账号** | AccountService 将 QQ/Web/Console 等多渠道统一到 account_id |
| **渠道感知记忆** | Hindsight 记忆携带完整渠道上下文（群组/私聊/频道），支持按场景过滤检索 |
| **存储层协议化** | typing.Protocol 定义 KeyValueStore / FileStore / PubSub，后端可任意替换 |
| **降级链** | 模型 API 故障时自动切换到备用模型，保证可用性 |
| **敏感信息保护** | API key 通过 `${ENV_VAR}` 占位符 + `.env` 文件注入，不进入 git 历史 |

## 记忆模块设计 (v8)

Hindsight 长期记忆的完整数据流设计，确保 NapCat 渠道的丰富元数据（发送者昵称、群名片、群名称、消息段类型等）完整传递到记忆层：

```
QQ OneBot JSON
  → NapCatChannel.normalize_message()   [增强] 提取 sender_name/card/role, group_name, 消息段摘要
    → UnifiedMessage                    [增强] 新增 12 个渠道富数据字段
      → Worker.handle_message()         [透传] 映射到 user_message dict
        → HarnessRunner.process_turn()  [封装] MemoryPayload{channel_context, session_context}
          → HindsightClient.retain()    [重写] 完整 context/tags/metadata/timestamp/observation_scopes
            → Hindsight 服务端           → 支持按渠道/群组/时间/范围过滤检索
```

详细设计见 [`docs/v8/hindsight-memory-best-practices.md`](docs/v8/hindsight-memory-best-practices.md)，NapCat API 数据能力参考 [`docs/v8/napcat-api-reference.md`](docs/v8/napcat-api-reference.md)。

## Docker 服务栈

| 服务 | 镜像 | 端口 | 用途 |
|------|------|------|------|
| **hpagent** | 本地构建 (`./src`) | 8082 | 主服务：WebSocket 服务 + Temporal Worker |
| **redis** | `redis:7-alpine` | 6379 | 会话热数据缓存 + PubSub |
| **temporal** | `temporalio/auto-setup:1.26.2` | 7233 | 工作流编排引擎 |
| **temporal-postgres** | `postgres:16-alpine` | — | Temporal 持久化数据库 |
| **temporal-web** | `temporalio/ui:2.34.0` | 8088 | Temporal Web 控制台 |
| **hindsight** | `ghcr.io/vectorize-io/hindsight:latest` | 8001 | 长期记忆：向量嵌入 + 语义检索 + 摘要 |
| **napcat** | `mlikiowa/napcat-docker:latest` | 6099 | QQ 机器人客户端（OneBot v11） |

## 快速开始

### 1. 配置密钥

编辑 `.env` 文件，填入真实的 API key：

```bash
MINIMAX_API_KEY=sk-cp-你的key
HINDSIGHT_LLM_API_KEY=sk-xxx    # 如只用本地 BGE-M3 embedding，可为占位符
```

### 2. 启动

```bash
docker compose up -d --build
```

### 3. 检查

```bash
# 各服务状态
docker compose ps

# Web 面板
# Temporal UI:  http://localhost:8088
# Hindsight:    http://localhost:8001/health
# HpAgent:      ws://localhost:8082
# NapCat WebUI: http://localhost:6099
```

### 4. NapCat QQ 登录

查看 NapCat 日志获取登录二维码，用手机 QQ 扫码。凭证自动持久化到 `channel/napcat/data/`，重启不丢失。

### 本地开发

```bash
cd src
pip install -r requirements.txt

# 需要先启动 Redis + Temporal（可复用 docker compose）
docker compose up -d redis temporal temporal-postgres temporal-web hindsight

# 本地启动 HpAgent
python main.py
```

## 技术栈

- **Python 3.11+** — asyncio 异步
- **Temporal** — 工作流编排，持久化执行，自动重试
- **nsjail** — OS 级沙箱隔离（PID/NET/FS namespace + rlimit）
- **Redis** — 会话热数据缓存 + PubSub 事件总线
- **Hindsight** — 长期记忆（BGE-M3 本地 embedding + LLM 摘要）
- **httpx** — 异步 HTTP 客户端
- **websockets** — NapCat WebSocket 通信
- **PostgreSQL** — Temporal 持久化存储
- **Docker Compose** — 一键部署全栈
