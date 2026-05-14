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
  harness/                   线束层（大脑拆解——无状态原子操作）
    ├── activities.py        5 个 Temporal Activity（上下文构建 / 工具列表 / 模型调用 /
    │                        工具执行 / 响应发送）
    └── context_builder.py   上下文构建器：事件流 → LLM messages（含系统提示词构造）
    ↓
  resources/                 资源层（模型调用池 + 凭据管理）
    ├── resource_pool.py     多模型注册 + 退避链调度 + HTTP 代理出口
    ├── model_client.py      单个模型 API 的 HTTP 客户端（流式 / 非流式）
    └── credentials.py       API 密钥加密存储 + 临时 token 签发
    ↓
  sandbox/                   沙箱层（手——工具执行 + 渠道 I/O）
    ├── sandbox.py           单个沙箱：工具注册表 + 执行隔离
    ├── sandbox_manager.py   沙箱生命周期管理（创建 / 销毁 / 空闲回收）
    ├── channels/            多渠道适配（NapCat QQ / Console / 可扩展）
    └── tools/               工具系统（BaseTool / ToolRegistry / ToolFactory）
    ↓
  session/                   会话存储层（Redis 热数据 + Hindsight 长期记忆 + 本地备份）
    ├── models.py            领域模型：Session / SessionStatus
    └── store.py             SessionStore：事件流 + 记忆召回/提取 + JSONL 文件备份
    ↓
  storage/                   存储层（持久化抽象——KV / 文件 / PubSub）
    ├── protocols.py         Protocol 定义 + StoreError 错误体系
    ├── file.py              AioFileStore（原子写入 + 路径沙箱）
    ├── postgres.py          SqlKeyValueStore（SQLAlchemy Core + 完整 DDL）
    ├── redis.py             RedisCache + RedisPubSub
    ├── _memory.py           InMemoryKVStore / InMemoryPubSub（开发回退）
    └── container.py         DI 容器（InfraContainer.build() 装配所有后端）
    ↓
  account/                   账号层（跨渠道用户身份统一）
    └── account_service.py   channel_type + sender_id → 统一 account_id
    ↓
  common/                    公共层（接口 / 类型 / 错误）
    ├── interfaces.py        IResources / ISandbox / IChannel / ITool 接口协议
    ├── types.py             枚举（ChannelType / EventType / StopReason）+
    │                        数据类（UnifiedMessage / ModelResponse / ToolCall）
    └── errors.py            统一异常体系（HpAgentError → 各子类错误）

============================================================================
关键设计决策
============================================================================

  1. 手脑分离: 编排层（大脑）只做决策，沙箱层（手）执行实际操作。
     模型调用通过 ResourcePool 退避链保证可用性，工具调用通过 Sandbox 隔离。
  2. Temporal Workflow: agentic loop 作为确定性 Workflow 运行，
     支持故障恢复、Signal 中断、Query 查询历史。
  3. 跨客户端记忆: 通过 AccountService 将 QQ/Web 等多渠道统一到 account_id，
     workflow_id = f"agent-{account_id}"，同一用户跨渠道共享一个 Workflow。
  4. 存储层协议化: 通过 typing.Protocol 定义 KeyValueStore / FileStore / PubSub，
     后端实现（PG / Redis / 文件 / 内存）可任意替换，核心协议零依赖。
"""
