# 03 — 组件架构（单 Agent）

> 对应视觉文件：[`diagrams/03_component.excalidraw`](diagrams/03_component.excalidraw)

本文是单 Agent 进程内部组件、职责与依赖关系的设计事实源；Excalidraw 只做人工维护的视觉表达。

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
| **职责** | 系统的组装工厂。按顺序构建所有运行依赖：① 日志初始化 → ② CredentialManager + ResourcePool -> 模型降级链配置 → ③ SessionStore (Redis + Hindsight + WAL) → ④ ToolVectorStore + ToolRetriever (ChromaDB) → ⑤ MCP ToolManager → ⑥ ToolRegistry + SandboxManager → ⑦ 渠道注册 (NapCat/Official/Console) → ⑧ TurnOrchestrator 组装 → ⑨ Temporal Worker 启动 |
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
| **对外接口** | `TurnOrchestrator.process_turn(session_id, user_message, event_history)` → 返回 LLM 响应文本 |
| **核心抽象** | `_rewrite_recall_query()` — HyDE 改写（将用户问题转换为声明式语句以提升向量检索命中率）；`_get_tools()` → `Sandbox.select_tools()` — 工具 RAG 选择；`_execute_tool()` → `Sandbox.execute()` — 工具执行路由；`_send_response()` → `ChannelRouter.send()` — 回复路由；`archive_session()`、`reflect()` — 生命周期管理 |
| **依赖** | `SessionStore`、`SandboxManager`、`ChannelRouter`、`ResourcePool`、`HarnessContextBuilder`、`MultiAgentExecutor`（条件分支，多 Agent 模式） |

**`harness/activities.py`** — Temporal Activity 薄封装

| 属性 | 说明 |
|------|------|
| **职责** | 5 个 `@activity.defn` 异步函数：`process_turn_activity`、`archive_session_activity`、`reflect_activity`、`reflect_batch_activity`、`metrics_report_activity`。每个函数仅做参数转发到 `_turn_orchestrator`（通过 `inject()` 设置的模块级 TurnOrchestrator） |
| **核心抽象** | 模块级单例注入：`inject(turn_orchestrator: TurnOrchestrator)` → 设置 `_turn_orchestrator`；Temporal Activity 通过装饰器注册，支持超时和重试策略 |
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
| 执行器 | `MultiAgentExecutor` | 桥接 TurnOrchestrator 和多 Agent 系统 |
| 通信 | `InMemoryMessageBus` | Agent 间消息传递（内存实现） |
| 补偿 | `CompensationRegistry` | 任务失败时的补偿/回滚处理器注册表 |
| 适配 | `ReActAgent` | 将 TurnOrchestrator 包装为 `BaseAgent` 接口 |

**当前状态**：`agent/` 包在单 Agent（QQ 渠道）模式下仅通过 `TurnOrchestrator` 中的条件分支引用 `MultiAgentExecutor`。完整的多 Agent 工作流将在 Web 渠道接入后启用。

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
| `agent/adapters.py` | `ReActAgent` | 将 TurnOrchestrator（单 Agent ReAct 模式）适配为多 Agent 系统中的 `BaseAgent` 接口 |
| `agent/factory.py` | `ResourcePoolAdapter` | 将 ResourcePool 的强类型 generate() 适配为多 Agent 系统期望的简化 `CallLLM` 协议 |
| `resources/model_client.py` | `ModelClient._convert_messages()`, `_tools_to_openai()`, `_tools_to_anthropic()` | 将 HpAgent 内部统一的 message 格式适配为不同 LLM API 格式（Anthropic vs OpenAI） |

### 4.5 模板方法模式（Template Method）

| 位置 | 参与类 | 解决的问题 |
|------|--------|-----------|
| `sandbox/channels/base.py` | `BaseChannel(IChannel)` | 定义消息渠道的骨架：`normalize_message()` + `send_message()` 为抽象方法（子类必须实现），`start_monitor()` / `stop_monitor()` 提供默认实现（子类可选覆写）。所有渠道遵循统一的生命周期 |

### 4.6 命令模式 / Activity 模式（Command）

| 位置 | 参与函数 | 解决的问题 |
|------|---------|-----------|
| `harness/activities.py` | `process_turn_activity`, `archive_session_activity`, `reflect_activity`, `reflect_batch_activity`, `metrics_report_activity` | 将 TurnOrchestrator 的业务方法封装为 Temporal Activity，使其获得 Temporal 提供的幂等性、超时、重试和分布式调度能力 |

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
| `orchestration/worker.py` | `start_worker()` 函数全量组装 | 所有组件通过构造函数/工厂接收依赖，不存在全局单例（除 Activity 薄封装层出于 Temporal 限制的模块级注入）。TurnOrchestrator 接收 SessionStore、SandboxManager、ChannelRouter、ResourcePool 全部作为构造参数 |
| `harness/activities.py` | `inject(turn_orchestrator)` 模块级函数 | 绕过 Temporal Activity 不能使用类实例的限制，通过模块级变量注入 TurnOrchestrator |

---

