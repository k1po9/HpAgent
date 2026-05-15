"""
HpAgent —— 带工具调用的智能体（Agent），基于"手脑分离"架构。

============================================================================
架构分层（自上而下）
============================================================================

  main.py                    入口：加载配置 → 启动 Worker
    ↓
  orchestration/             编排层（大脑——决策与流程控制）
    ├── workflow.py          Temporal Workflow：agentic loop 的确定性编排
    └── worker.py            Temporal Worker：依赖初始化 + Activity 注册 + 渠道监听
    ↓
  harness/                   线束层（大脑——无状态协调器）
    ├── activities.py        3 个 Temporal Activity（薄封装，委托 HarnessRunner）
    ├── runner.py            HarnessRunner：无状态协调器（完整 agentic loop）
    ├── context_builder.py   上下文构建器：事件流 → LLM messages
    └── prompts.py           PromptLoader：从 YAML 加载 prompt 模板
    ↓
  resources/                 资源层（模型调用池 + 凭据管理）
    ├── resource_pool.py     多模型注册 + 退避链调度
    ├── model_client.py      单个模型 API 的 HTTP 客户端
    └── credentials.py       API 密钥管理 + 临时 token
    ↓
  sandbox/                   沙箱层（手——工具执行 + 渠道 I/O）
    ├── sandbox.py           单个沙箱：工具注册表 + nsjail 执行
    ├── sandbox_manager.py   沙箱生命周期管理（创建/销毁/空闲回收）
    ├── nsjail.py            NsjailConfig + NsjailExecutor：OS 级隔离
    ├── runner.py            in-jail 工具调度脚本（nsjail 子进程内运行）
    ├── channels/            多渠道适配（NapCat QQ / Console）
    └── tools/               工具体系（BaseTool / ToolRegistry / ToolFactory）
    ↓
  session/                   会话层（记忆）
    ├── store.py             SessionStore：Redis 事件流 + Hindsight 长期记忆 + JSONL 备份
    └── models.py            Session / SessionStatus 领域模型
    ↓
  storage/                   存储层（仅 Redis 后端）
    ├── redis.py             RedisCache + RedisPubSub
    └── protocols.py         存储协议定义（架构参考）
    ↓
  memory/                    长期记忆
    └── hindsight_client.py  Hindsight HTTP 客户端（retain / recall / reflect）
    ↓
  workspace/                 多用户工作区
    ├── manager.py           WorkspaceManager：目录骨架 + nsjail bind mount
    ├── db.py                WorkspaceDB：SQLite 元数据
    └── models.py            User / Session / Artifact 数据模型
    ↓
  account/                   跨渠道账号
    └── account_service.py   channel_type + sender_id → account_id
    ↓
  common/                    公共层
    ├── types.py             枚举 + 数据类（Event / UnifiedMessage / ToolCall 等）
    ├── interfaces.py        核心接口 ABC（IResources / ISandbox / IChannel / ITool）
    ├── errors.py            统一异常体系（AgentError → 子类）
    └── logging.py           双 sink 日志（控制台 + JSONL 文件）

============================================================================
关键设计决策
============================================================================

  1. 手脑分离: 编排层（大脑）只做决策，沙箱层（手）执行实际操作。
     模型调用通过 ResourcePool 退避链保证可用性，工具调用通过 nsjail 隔离。
  2. Temporal Workflow: agentic loop 作为确定性 Workflow 运行，
     支持故障恢复、Signal 中断、Query 查询状态。
  3. 跨渠道统一账号: AccountService 将 QQ/Web/Console 多渠道统一到 account_id。
  4. 全链路降级: Redis 不可用→内存回退，Hindsight 不可用→记忆静默跳过，
     模型不可用→退避链自动切换。
  5. 敏感信息保护: API key 通过 ${ENV_VAR} 占位符 + .env 文件注入，不入 git。
"""
