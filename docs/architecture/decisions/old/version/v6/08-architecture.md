# HpAgent 项目架构全览

> 版本: v6  
> 日期: 2026-05-09  
> 总文件数: 46 source files + 3 test files  
> 总测试数: 65 (22 nsjail + 23 workspace + 20 hindsight)

---

## 目录

1. [架构总览](#1-架构总览)
2. [模块依赖图](#2-模块依赖图)
3. [启动时序图](#3-启动时序图)
4. [消息处理全流程](#4-消息处理全流程)
5. [Agentic Loop 内部流程](#5-agentic-loop-内部流程)
6. [工具执行沙箱流程](#6-工具执行沙箱流程)
7. [工作区生命周期](#7-工作区生命周期)
8. [记忆系统数据流](#8-记忆系统数据流)
9. [Docker 部署拓扑](#9-docker-部署拓扑)
10. [目录结构](#10-目录结构)
11. [模块详解](#11-模块详解)
12. [数据模型字典](#12-数据模型字典)
13. [配置参考](#13-配置参考)
14. [接口抽象层](#14-接口抽象层)
15. [测试覆盖](#15-测试覆盖)

---

## 1. 架构总览

### 1.1 五层架构

```
┌──────────────────────────────────────────────────────────────┐
│                      渠道层 (Channels)                        │
│   NapCat/QQ · Web · Console · ChannelRouter                  │
│   消息进入系统 & 回复送回用户                                     │
└───────────────────────────┬──────────────────────────────────┘
                            │ UnifiedMessage
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                    编排层 (Orchestration)                      │
│   worker.py → Temporal Workflow → agentic loop               │
│   初始化依赖 · 注入 Activities · 启动 Worker · 渠道监听           │
└───────────────────────────┬──────────────────────────────────┘
                            │ Temporal Activities
                            ▼
┌──────────────────────────────────────────────────────────────┐
│                   指挥层 (Harness / Brain)                     │
│   activities.py · context_builder.py                         │
│   构建上下文 · 调用模型 · 执行工具 · 发送响应 · 记忆管理           │
└───────┬───────────────────┬───────────────────┬──────────────┘
        │                   │                   │
   LLM API             nsjail 沙箱          Hindsight 记忆
        │                   │                   │
┌───────▼───────┐  ┌────────▼────────┐  ┌───────▼──────────────┐
│  资源层        │  │  沙箱层 (Hands)  │  │  记忆层 (Memory)      │
│  ResourcePool │  │  SandboxManager │  │  HindsightClient     │
│  Credentials  │  │  NsjailExecutor │  │  retain/recall/      │
│  ModelClient  │  │  runner.py      │  │  reflect             │
└───────────────┘  └────────┬────────┘  └──────────────────────┘
                            │ bind mounts
                    ┌───────▼────────┐
                    │  工作区层        │
                    │  WorkspaceMgr  │
                    │  WorkspaceDB   │
                    │  SQLite + FS   │
                    └────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                    基础设施层 (Infrastructure)                  │
│   Temporal Server · PostgreSQL · Redis · pgvector · Docker    │
│   common/types · common/interfaces · storage/ · session/      │
│   account/ · resources/                                       │
└──────────────────────────────────────────────────────────────┘
```

### 1.2 "手脑分离"设计哲学

```
┌───────────────┐        ┌──────────────────────┐
│   大脑 (Brain)  │──────▶│    双手 (Hands)       │
│               │ 决定   │                      │
│  context      │ 用什么  │  SandboxManager      │
│  builder      │ 工具   │  NsjailExecutor       │
│  activities   │       │  runner.py (in jail)  │
│               │       │                      │
│  LLM 推理     │       │  工具实际执行           │
│  策略选择     │       │  OS 级隔离             │
└───────────────┘       └──────────────────────┘
        │                        │
        │     Temporal Activities│
        │     (确定性编排边界)     │
        │                        │
        ▼                        ▼
   call_model        execute_tool_activity
   _activity         → nsjail subprocess
```

---

## 2. 模块依赖图

```
src/
├── main.py                         ◀── 入口，加载配置，调用 start_worker()
│
├── orchestration/
│   ├── worker.py                   ◀── 初始化所有依赖 + 注入 + 启动 Worker
│   │   depends: harness, resources, sandbox, account, session, workspace, memory
│   └── workflow.py                 ◀── Temporal Workflow 定义 (agentic loop)
│       depends: harness.activities (通过 Activity 名调用)
│
├── harness/
│   ├── activities.py               ◀── 11 个 Temporal Activities
│   │   depends: context_builder, sandbox_manager, resource_pool,
│   │            channel_router, redis_cache, workspace_manager, hindsight_client
│   └── context_builder.py          ◀── events[] → LLM messages[]
│       depends: common.types, .hermes.md / CLAUDE.md 文件加载
│
├── sandbox/                        ◀── OS 级工具执行隔离
│   ├── nsjail.py                   ◀── NsjailConfig + NsjailExecutor
│   ├── runner.py                   ◀── 在 nsjail 内执行的工具调度器
│   ├── sandbox.py                  ◀── 单个沙箱 (ISandbox 实现)
│   ├── sandbox_manager.py          ◀── 沙箱池管理器
│   ├── tools/
│   │   ├── base.py                 ◀── BaseTool / ToolDefinition / ToolResult
│   │   ├── factory.py              ◀── 动态工具创建 + 内置工具
│   │   └── registry.py             ◀── ToolRegistry (工具注册表)
│   └── channels/
│       ├── base.py                 ◀── BaseChannel 抽象
│       ├── napcat.py               ◀── NapCat QQ 渠道 (WebSocket)
│       ├── console.py              ◀── 控制台渠道
│       └── router.py               ◀── ChannelRouter 渠道路由
│
├── workspace/                      ◀── 多用户持久化工作目录
│   ├── models.py                   ◀── User / Session / Artifact / SessionStatus
│   ├── db.py                       ◀── WorkspaceDB (SQLite + WAL)
│   └── manager.py                  ◀── WorkspaceManager (目录 + nsjail 集成)
│
├── memory/                         ◀── Hindsight 记忆系统客户端
│   ├── hindsight_client.py         ◀── retain / recall / reflect API
│
├── resources/                      ◀── 模型资源管理
│   ├── credentials.py              ◀── CredentialManager (API Key + Token)
│   ├── model_client.py             ◀── ModelClient (Anthropic/OpenAI 统一接口)
│   └── resource_pool.py            ◀── ResourcePool (退避链)
│
├── session/                        ◀── 会话管理
│   ├── models.py                   ◀── 请求/响应模型
│   ├── repositories.py             ◀── 仓库层抽象
│   └── session_manager.py          ◀── TemporalSessionManager (ISession 实现)
│
├── storage/                        ◀── 存储后端抽象
│   ├── protocols.py                ◀── 存储协议定义
│   ├── postgres.py                 ◀── PostgreSQL 实现
│   ├── file.py                     ◀── 文件存储实现
│   ├── redis.py                    ◀── Redis 缓存实现
│   ├── _memory.py                  ◀── 内存存储 (开发/测试)
│   └── container.py                ◀── 存储容器 (依赖注入)
│
├── account/                        ◀── 账号映射
│   ├── models.py
│   └── account_service.py          ◀── 渠道 ID → 统一 account_id
│
└── common/                         ◀── 共享基础设施
    ├── types.py                    ◀── Event / UnifiedMessage / ToolCall / ...  (293行)
    ├── interfaces.py               ◀── ISession / IResources / ISandbox / IChannel / ITool
    └── errors.py                   ◀── 自定义异常
```

### 2.1 import 方向 (单向)

```
main.py
  └─ orchestration/worker
       ├─ orchestration/workflow     ◀── 只被 start_workflow 引用
       ├─ harness/activities         ◀── 被 Worker 注册
       │    ├─ harness/context_builder
       │    ├─ sandbox/sandbox_manager
       │    │    ├─ sandbox/sandbox
       │    │    │    └─ sandbox/tools/{base,registry}
       │    │    └─ sandbox/nsjail
       │    ├─ resources/resource_pool
       │    ├─ workspace/manager
       │    ├─ memory/hindsight_client
       │    └─ sandbox/channels/router
       ├─ account/account_service
       ├─ session/session_manager
       └─ common/{types,interfaces,errors}
```

---

## 3. 启动时序图

```
main.py                worker.py            Temporal Server     NapCat
   │                      │                      │               │
   ├─ load_config()       │                      │               │
   ├─ start_worker()─────▶│                      │               │
   │                      │                      │               │
   │                      ├─ init_dependencies() │               │
   │                      │  ├─ CredentialManager│               │
   │                      │  ├─ ResourcePool.init│               │
   │                      │  ├─ Redis (optional) │               │
   │                      │  ├─ NsjailConfig     │               │
   │                      │  ├─ WorkspaceManager │               │
   │                      │  ├─ HindsightClient  │               │
   │                      │  ├─ SandboxManager   │               │
   │                      │  ├─ ContextBuilder   │               │
   │                      │  ├─ AccountService   │               │
   │                      │  ├─ SessionManager   │               │
   │                      │  └─ ChannelRouter    │               │
   │                      │                      │               │
   │                      ├─ inject(deps) ──▶ activities.py     │
   │                      │                      │               │
   │                      ├─ Client.connect()───▶│               │
   │                      │                      │               │
   │                      ├─ Worker(client,      │               │
   │                      │    task_queue=        │               │
   │                      │    "hpagent-...",     │               │
   │                      │    workflows=[Orch..],│               │
   │                      │    activities=[11个]) │               │
   │                      │                      │               │
   │                      ├─ NapCatChannel()     │               │
   │                      ├─ router.register()   │               │
   │                      │                      │               │
   │                      ├─ napcat.start_       │               │
   │                      │   monitor(callback)──┼──────────────▶│ (ws://0.0.0.0:8082)
   │                      │                      │               │
   │                      ├─ async with worker:  │               │
   │                      │   await Future()     │               │
   │                      │   (永久运行)          │               │
```

---

## 4. 消息处理全流程

### 4.1 新会话启动 (首条消息)

```
NapCat/QQ User
   │
   │  "帮我计算 2+2"
   │
   ▼
NapCatChannel (WebSocket ws://0.0.0.0:8082)
   │
   │ normalize_message(raw_json) → UnifiedMessage
   │   channel_type=NAPCAT
   │   sender_id=QQ号
   │   content="帮我计算 2+2"
   │
   ▼
handle_message(UnifiedMessage)          ◀── worker.py 回调
   │
   ├─ content.strip() empty? → skip
   │
   ├─ AccountService.resolve("napcat", QQ号)
   │    └─ → account_id="u_<hash>"
   │
   ├─ SessionManager.list_active_sessions()
   │    └─ → session_id="" (新用户，无活跃会话)
   │
   ├─ SessionManager.create_session_with_id()
   │    └─ → session_id="sess_<ts>_<random>"
   │
   ├─ WorkspaceManager.ensure_user(account_id)
   │    └─ 创建 users_workspace/<uuid>/
   │       ├── skills/
   │       ├── sessions/
   │       ├── persistent/
   │       └── user_profile.yaml
   │
   ├─ WorkspaceManager.create_session(account_id, session_id)
   │    └─ 创建完整的 11 个子目录
   │
   ├─ SandboxManager.create_session_sandbox(account_id, session_id)
   │    └─ 创建 NsjailConfig( bind_mounts=[
   │         "/host/.../workspace:/work",
   │         "/host/.../skills:/skills:ro" ])
   │
   ├─ workflow_id = "agent-<account_id>"
   │
   ├─ client.start_workflow(
   │     OrchestrationWorkflow.run,
   │     user_message={
   │       content, sender_id, channel_type,
   │       session_id, account_id,
   │       workspace_user_uuid,    ◀── 传递给 Workflow
   │       workspace_session_id,   ◀── 传递给 Workflow
   │     },
   │     id="agent-<account_id>",
   │     task_queue="hpagent-task-queue")
   │
   └─▶ Temporal Server ──▶ OrchestrationWorkflow.run()
```

### 4.2 后续消息 (已存在 Workflow)

```
NapCat/QQ User
   │
   │  "再帮我算 3+5"
   │
   ▼
handle_message(UnifiedMessage)
   │
   ├─ AccountService.resolve() → account_id
   ├─ session_id = 活跃会话的 session_id
   ├─ workflow_id = "agent-<account_id>"
   │
   ├─ try: client.start_workflow(...)
   │    └─▶ Temporal: WorkflowAlreadyStartedError!
   │
   ├─ except WorkflowAlreadyStartedError:
   │    handle = client.get_workflow_handle(workflow_id)
   │    handle.signal(OrchestrationWorkflow.new_message, user_message)
   │
   └─▶ Temporal ──▶ workflow.new_message(user_message)
        │
        ├─ self._events.append(USER_MESSAGE)
        ├─ self._pending_messages.append(user_message)
        │
        └─▶ wait_condition 唤醒 → _process_turn(next_msg)
```

---

## 5. Agentic Loop 内部流程

### 5.1 _process_turn() 7 步循环

```
_process_turn(user_message)
│
│  account_id = user_message["account_id"]
│  session_id = user_message["session_id"]
│  turn_events = []                    ◀── 用于 retain 的事件收集
│
│  ┌─────────────────────────────────────────────────────────────┐
│  │  LOOP: while turns < max_turns AND not completed:            │
│  │                                                              │
│  │  ┌───────────────────────────────────────────────────────┐   │
│  │  │ Step 1: RECALL 记忆                                    │   │
│  │  │                                                         │   │
│  │  │  recall_activity(query, account_id, session_id, 5)     │   │
│  │  │    └─ HindsightClient.recall()                         │   │
│  │  │         POST /api/v1/recall                             │   │
│  │  │          语义向量 + BM25 + 图谱 + 时序                     │   │
│  │  │          → Reranker 精排 (bge-reranker-v2-m3)          │   │
│  │  │    ← {memories: [...], formatted: "# 相关记忆\n..."}   │   │
│  │  │                                                         │   │
│  │  │  降级: Hindsight 不可用 → 返回 {"memories":[]}         │   │
│  │  ├───────────────────────────────────────────────────────┤   │
│  │  │ Step 2: BUILD 上下文                                    │   │
│  │  │                                                         │   │
│  │  │  build_context_activity(events, channel, memories)      │   │
│  │  │    └─ HarnessContextBuilder.build(recalled_memories=)   │   │
│  │  │         system_prompt =                                 │   │
│  │  │           渠道身份 (NAPCAT/CONSOLE/WEB)                  │   │
│  │  │           + 风格提示 (chat/CLI)                          │   │
│  │  │           + 跨渠道检测                                    │   │
│  │  │           + 工具使用纪律                                   │   │
│  │  │           + 环境感知 (Docker/WSL)                         │   │
│  │  │           + # 相关记忆 ←─── recalled_memories 注入点      │   │
│  │  │           + 项目上下文 (.hermes.md/CLAUDE.md)            │   │
│  │  │    ← [{"role":"system",...}, {"role":"user",...}, ...]  │   │
│  │  ├───────────────────────────────────────────────────────┤   │
│  │  │ Step 3: LIST 工具                                       │   │
│  │  │                                                         │   │
│  │  │  get_available_tools_activity()                         │   │
│  │  │    └─ 遍历 SandboxManager 中所有 active 沙箱             │   │
│  │  │         → sandbox.list_tools() → ToolRegistry           │   │
│  │  │    ← [{"type":"function","function":{...}}, ...]        │   │
│  │  ├───────────────────────────────────────────────────────┤   │
│  │  │ Step 4: CALL 模型                                       │   │
│  │  │                                                         │   │
│  │  │  call_model_activity(context, tools)                    │   │
│  │  │    └─ ResourcePool.generate(messages, tools)            │   │
│  │  │         → 主模型 → 失败? → 退避链 → 备用模型              │   │
│  │  │    ← {content, tool_calls, stop_reason, usage}          │   │
│  │  │                                                         │   │
│  │  │  追加 MODEL_MESSAGE 到 self._events[]                   │   │
│  │  │  追加 assistant event 到 turn_events[]                  │   │
│  │  ├───────────────────────────────────────────────────────┤   │
│  │  │ Step 5: EXECUTE 工具 (如果有 tool_calls)               │   │
│  │  │                                                         │   │
│  │  │  FOR each tool_call:                                    │   │
│  │  │    execute_tool_activity(tool_name, arguments)          │   │
│  │  │      └─ SandboxManager → 查找注册了该工具的沙箱          │   │
│  │  │           → Sandbox.execute() → NsjailExecutor         │   │
│  │  │              → nsjail subprocess → runner.py            │   │
│  │  │    ← {output, error, execution_id}                      │   │
│  │  │                                                         │   │
│  │  │    追加 TOOL_RESULT 到 self._events[]                   │   │
│  │  │    → 回到 Step 1 (新的上下文包含工具结果)                  │   │
│  │  └───────────────────────────────────────────────────────┘   │
│  │                                                              │
│  │  IF 无 tool_calls: turn_completed = True → 退出循环          │
│  └─────────────────────────────────────────────────────────────┘
│
├─ Step 6: SEND 响应
│    send_response_activity(final_content, user_message)
│      └─ 从 user_message 提取 channel_type
│           → UnifiedMessage → ChannelRouter.send()
│              → NapCatChannel.send_message() (WebSocket)
│                 → OneBot API (send_group_msg / send_private_msg)
│    → 用户收到回复
│
└─ Step 7: RETAIN 记忆
     retain_activity(turn_events, account_id, session_id)
       └─ HindsightClient.retain()
            POST /api/v1/retain
              → LLM 提取: 偏好/事实/决策/关系
              → bge-m3 Embedding → pgvector
       ← {stored: N}

     降级: Hindsight 不可用 → 返回 {"stored":0}
```

### 5.2 LLM 看到的最終 system prompt 结构

```
┌─────────────────────────────────────────┐
│  # 渠道身份声明                           │
│  "你是 nono，一只会说话的猫..."             │ ← NAPCAT_AGENT_IDENTITY
├─────────────────────────────────────────┤
│  # 风格提示 (可选)                        │
│  CHAT_PERSONALITY_GUIDANCE (NapCat)     │
├─────────────────────────────────────────┤
│  # 跨渠道检测 (可选)                      │
│  "用户正在通过多个客户端与你对话"             │ ← 仅当多端同时活跃
├─────────────────────────────────────────┤
│  # 工具使用纪律 (可选)                     │
│  "你必须使用工具来执行操作..."              │ ← TOOL_USE_ENFORCEMENT_GUIDANCE
├─────────────────────────────────────────┤
│  # 环境感知                              │
│  "你运行在 Linux Docker 容器内"            │ ← DOCKER/WSL_ENVIRONMENT_HINT
├─────────────────────────────────────────┤
│  # 相关记忆 (Hindsight recall)            │ ← ★ v6 新增
│  - [preference] 用户偏好简洁回答            │
│  - [fact] 用户正在开发 Go 后端             │
├─────────────────────────────────────────┤
│  # 项目上下文                            │
│  .hermes.md / CLAUDE.md / .cursorrules  │ ← 从文件系统加载
│  SOUL.md                                │
└─────────────────────────────────────────┘
```

---

## 6. 工具执行沙箱流程

### 6.1 nsjail 隔离执行

```
execute_tool_activity("calculator", {"expression":"2+2"})
│
├─ SandboxManager.list_sandboxes()
│    └─ 找到注册了 "calculator" 的 active 沙箱
│
├─ Sandbox.execute("calculator", {"expression":"2+2"})
│    │
│    │ ToolRegistry.has("calculator")? ✓
│    │
│    └─▶ NsjailExecutor.execute("calculator", {"expression":"2+2"})
│          │
│          ├─ execution_id = uuid4()
│          │
│          ├─ NsjailConfig.build_command(
│          │     tool_name="calculator",
│          │     arguments={"expression":"2+2"},
│          │     extra_bind_mounts=[
│          │       "/host/.../workspace:/work",       ← rw
│          │       "/host/.../skills:/skills"]        ← ro
│          │     override_work_dir="/work"
│          │   )
│          │
│          │   → 生成完整 nsjail 命令:
│          │
│          │   ┌─────────────────────────────────────────────┐
│          │   │ /usr/bin/nsjail                             │
│          │   │   --mode o                                  │
│          │   │   --chroot /                                │
│          │   │   --hostname sandbox                        │
│          │   │   --cwd /work                               │
│          │   │   --user nobody --group nogroup             │
│          │   │   --time_limit 30                           │
│          │   │   --rlimit_as 256                           │
│          │   │   --rlimit_cpu 10                           │
│          │   │   --rlimit_nofile 64                        │
│          │   │   --rlimit_nproc 32                         │
│          │   │   --disable_proc                            │
│          │   │   --iface_no_lo                             │
│          │   │   --really_quiet                            │
│          │   │   --bindmount /host/.../workspace:/work     │
│          │   │   --bindmount_ro /host/.../skills:/skills   │
│          │   │   --                                        │
│          │   │   /usr/bin/python3                          │
│          │   │   /work/runner.py                           │
│          │   │   calculator                                │
│          │   │   '{"expression":"2+2"}'                    │
│          │   └─────────────────────────────────────────────┘
│          │
│          ├─ asyncio.create_subprocess_exec(*cmd)
│          │    │
│          │    │  ┌─────────── nsjail PID namespace ───────────┐
│          │    │  │  user: nobody                              │
│          │    │  │  chroot: / (read-only)                     │
│          │    │  │  /proc: disabled                           │
│          │    │  │  network: lo only (disabled)               │
│          │    │  │  /work → /host/.../workspace (rw)          │
│          │    │  │  /skills → /host/.../skills (ro)           │
│          │    │  │                                            │
│          │    │  │  $ python3 /work/runner.py calculator \    │
│          │    │  │      '{"expression":"2+2"}'                │
│          │    │  │                                            │
│          │    │  │    → runner.py main():                     │
│          │    │  │        1. 解析 JSON 参数                   │
│          │    │  │        2. TOOLS["calculator"] → _tool_calc │
│          │    │  │        3. eval("2+2", {"__builtins__":{}}) │
│          │    │  │           → "4"                            │
│          │    │  │        4. stdout: {"success":true,         │
│          │    │  │              "output":"4"}                  │
│          │    │  └────────────────────────────────────────────┘
│          │    │
│          │    ├─ await proc.communicate() (timeout: 35s)
│          │    │
│          │    ├─ stdout = '{"success":true, "output":"4"}'
│          │    └─ stderr = ""
│          │
│          ├─ _parse_runner_output(stdout)
│          │    └─ json.loads → ToolResult(success=True, output="4")
│          │
│          ├─ (optional) _persist_result → Redis
│          │    key: "sandbox:result:<execution_id>"
│          │    TTL: 3600s
│          │
│          └─ return ToolResult
│
└─ return {"output":"4", "error":null, "execution_id":"<uuid>"}
```

### 6.2 runner.py 安全措施

```
runner.py 在 nsjail 内执行的安全边界:
┌────────────────────────────────────────────────────┐
│ ✓ PID namespace:  独立进程空间，无法看到/kill 宿主进程   │
│ ✓ chroot:         文件系统隔离，默认只读                │
│ ✓ --bindmount:    仅暴露 workspace/ (rw) + skills/ (ro)│
│ ✓ --user nobody:  非特权用户运行                       │
│ ✓ --disable_proc: 无法访问 /proc (防信息泄漏)           │
│ ✓ --iface_no_lo:  网络完全禁用                         │
│ ✓ rlimit_as:      256MB 内存硬限制                     │
│ ✓ rlimit_cpu:     10s CPU 时间硬限制                   │
│ ✓ rlimit_nofile:  最多打开 64 个文件                    │
│ ✓ rlimit_nproc:   最多 32 个进程                       │
│ ✓ eval() 保护:    __builtins__={} 空字典               │
│ ✓ time_limit:     30s 超时 SIGKILL                    │
└────────────────────────────────────────────────────┘
```

---

## 7. 工作区生命周期

### 7.1 目录生命周期状态机

```
                   ┌──────────┐
    ensure_user()  │  ACTIVE  │  create_session()
    ──────────────▶│          │◀──────────────────
                   └─────┬────┘
                         │
                    end_session()
                    ┌────┴────┐
                    ▼         ▼
              ┌──────────┐ ┌──────────┐
              │COMPLETED │ │  FAILED  │
              └────┬─────┘ └────┬─────┘
                   │            │
                   └─────┬──────┘
                         │
              cleanup_expired_sessions()
              (max_age_days 后自动清理)
                         │
                         ▼
                   ┌──────────┐
                   │ DELETED  │  (目录已删除，DB 记录保留)
                   └──────────┘
```

### 7.2 磁盘布局

```
users_workspace/                         ◀── workspace_root (config)
├── workspace.db                         ◀── SQLite (WAL 模式)
│   ├── users                            ◀── 用户注册表
│   ├── sessions                         ◀── 会话元数据 + 状态
│   │   └── idx_sessions_user_created    ◀── 复合索引
│   └── artifacts                        ◀── 产出物索引
│       └── idx_artifacts_session        ◀── 索引
│
├── <user_uuid_1>/                       ◀── ensure_user() 创建
│   ├── user_profile.yaml                ◀── 用户偏好
│   ├── skills/                          ◀── 自定义技能 (ro mount)
│   │   └── *.yaml
│   ├── persistent/                      ◀── 跨会话持久文件
│   └── sessions/
│       ├── sess_<ts>_<rand1>/
│       │   ├── session.yaml             ◀── {session_id, status, ...}
│       │   ├── conversation/
│       │   │   ├── messages.jsonl       ◀── 对话记录
│       │   │   └── summary.md           ◀── 自动摘要
│       │   ├── execution/
│       │   │   ├── plan.yaml            ◀── 工具调用计划
│       │   │   └── logs/                ◀── 步骤 stdout/stderr
│       │   ├── workspace/               ◀── nsjail --bindmount → /work
│       │   │   ├── input/               ◀── 初始输入
│       │   │   ├── scratch/             ◀── 中间文件
│       │   │   └── output/              ◀── 最终产出
│       │   └── resources/
│       │       └── resource_manifest.yaml
│       └── sess_<ts>_<rand2>/
│           └── ...
│
└── <user_uuid_2>/
    └── ...
```

---

## 8. 记忆系统数据流

### 8.1 三类记忆操作

```
┌─────────────────────────────────────────────────────────────┐
│                      记忆操作全景                             │
├───────────────┬─────────────────┬─────────────────────────────┤
│   RECALL      │    RETAIN       │      REFLECT               │
│   (每轮调用)   │   (每轮异步)     │    (定时触发)               │
├───────────────┼─────────────────┼─────────────────────────────┤
│ 关键路径       │ 非关键路径        │ 完全后台                      │
│ 超时 10s      │ 超时 30s         │ 超时 60s                      │
│ 调用 LLM? NO  │ 调用 LLM? YES    │ 调用 LLM? YES                │
│ 延迟 ~100ms   │ 延迟 ~500ms      │ 延迟 ~5s                     │
│               │                 │                              │
│ 输入:         │ 输入:            │ 输入:                        │
│  query=用户消息│  events=本轮对话  │  user_id=用户ID              │
│  + user_id    │  + user_id       │                              │
│  + session_id │  + session_id    │ 输出:                        │
│               │                 │  insights=N (新洞察数量)      │
│ 输出:         │ 输出:            │                              │
│  memories=[]  │  stored=N        │ 处理:                        │
│  formatted=   │                 │  记忆关联 + 矛盾检测           │
│  prompt text  │ 处理:            │  + 知识抽象 + 经验总结         │
│               │  LLM提取→Embedding│                              │
│ 检索路径:      │  →pgvector存储   │ 触发:                        │
│  语义向量      │                 │  Temporal Schedule            │
│  + BM25       │ 记忆类型:        │  每 6h 为活跃用户触发          │
│  + 知识图谱    │  preference     │                              │
│  + 时序衰减    │  / fact         │                              │
│  → Reranker   │  / decision     │                              │
│               │  / relationship │                              │
├───────────────┼─────────────────┼─────────────────────────────┤
│ 降级: 返回[]   │ 降级: 返回 0     │ 降级: 返回 0                  │
└───────────────┴─────────────────┴─────────────────────────────┘
```

### 8.2 Hindsight 服务内部

```
┌──────────────────────────────────────────────┐
│             Hindsight Service                 │
│              (Port 8000)                      │
│                                               │
│  POST /api/v1/recall                          │
│    ├─ 语义检索: pgvector cosine(query_vec)     │
│    ├─ 关键词:  BM25 inverted index            │
│    ├─ 图谱:    实体关系遍历                     │
│    ├─ 时序:    衰减加权                        │
│    └─ Reranker: bge-reranker-v2-m3 精排       │
│                                               │
│  POST /api/v1/retain                          │
│    ├─ LLM 提取: 偏好/事实/决策/关系             │
│    ├─ Embedding: BAAI/bge-m3 (1024d)          │
│    └─ 存储: pgvector INSERT                   │
│                                               │
│  POST /api/v1/reflect                         │
│    ├─ LLM 推理: 记忆关联/矛盾检测               │
│    ├─ 知识抽象: 碎片→高层                       │
│    └─ 清理: 低价值/过期记忆                     │
└──────────────┬───────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────┐
│       hindsight-postgres (pgvector)           │
│              (Port 5432)                      │
│                                               │
│  pgvector 扩展的 PostgreSQL 16                │
│  - 向量索引: IVFFlat / HNSW                   │
│  - 元数据列: user_id, type, relevance, ts     │
└──────────────────────────────────────────────┘
```

---

## 9. Docker 部署拓扑

```
                    ┌──────────────────────┐
                    │   宿主机 (Host)       │
                    │                      │
                    │  Port 8082 ◀── NapCat WebSocket 入口
                    │  Port 6099 ◀── NapCat WebUI
                    │  Port 7233 ◀── Temporal gRPC
                    │  Port 8088 ◀── Temporal Web UI
                    │  Port 8001 ◀── Hindsight API (调试)
                    └──────┬───────────────┘
                           │
              ┌────────────┴────────────┐
              │    app-network (bridge)  │
              └────────────┬────────────┘
                           │
    ┌──────────────────────┼──────────────────────────┐
    │                      │                          │
    ▼                      ▼                          ▼
┌─────────┐    ┌──────────────────┐    ┌──────────────────┐
│ napcat  │    │    hpagent       │    │   temporal        │
│ (QQ)    │    │  (sysbox-runc)   │    │  (auto-setup)     │
│         │    │                  │    │  port: 7233       │
│         │    │  → NapCat WS     │    └────────┬─────────┘
│         │    │  → ContextBuilder│             │
│         │    │  → ResourcePool  │    ┌────────▼─────────┐
│         │    │  → SandboxManager│    │ temporal-        │
│         │    │  → nsjail exec   │    │ postgresql       │
│         │    │  → WorkspaceMgr  │    │ (pg:16)          │
│         │    │  → HindsightClnt │    └──────────────────┘
└─────────┘    └────────┬─────────┘
                         │
                         │ recall/retain/reflect
                         ▼
              ┌──────────────────┐
              │   hindsight      │
              │   (API: 8000)    │
              │                  │
              │  EMBEDDING:      │
              │  BAAI/bge-m3     │
              │  RERANKER:       │
              │  bge-reranker-v2 │
              └────────┬─────────┘
                       │
                       ▼
              ┌──────────────────┐
              │ hindsight-       │
              │ postgresql       │
              │ (pgvector:pg16)  │
              └──────────────────┘

网络隔离:
  - hpagent ↔ napcat:        app-network (WebSocket)
  - hpagent ↔ temporal:      app-network (gRPC :7233)
  - hpagent ↔ hindsight:     app-network (HTTP :8000)
  - hindsight ↔ pgvector:    app-network (PG :5432)
  - 外部 → hpagent:          8082 (NapCat WebSocket)
  - 外部 → hindsight:        8001 (仅调试)
```

---

## 10. 目录结构

```
HpAgent/
├── README.md                          ◀── 项目说明
├── docker-compose.yaml                ◀── 8 个服务定义
├── config/
│   ├── config.yaml                    ◀── 应用配置 (model + app + hindsight)
│   └── dynamicconfig/
│       └── development-sql.yaml       ◀── Temporal 动态配置
│
├── src/                               ◀── 源代码根目录
│   ├── Dockerfile                     ◀── hpagent 镜像构建
│   ├── entrypoint.sh                  ◀── 容器启动脚本
│   ├── requirements.txt               ◀── Python 依赖 (5个 + hindsight-client)
│   ├── main.py                        ◀── 入口: load_config → start_worker (140行)
│   │
│   ├── common/                        ◀── 共享基础设施 (3 文件, ~500行)
│   │   ├── types.py                   ◀── Event/UnifiedMessage/ModelResponse/... (293行)
│   │   ├── interfaces.py              ◀── ISession/IResources/ISandbox/IChannel/ITool (288行)
│   │   └── errors.py                  ◀── 自定义异常类
│   │
│   ├── orchestration/                 ◀── 编排层 (2 文件, ~600行)
│   │   ├── worker.py                  ◀── 依赖初始化 + Worker 启动 + 渠道监听 (340行)
│   │   └── workflow.py                ◀── OrchestrationWorkflow (Temporal) (285行)
│   │
│   ├── harness/                       ◀── 指挥层 (2 文件, ~930行)
│   │   ├── activities.py              ◀── 11 个 Temporal Activities (430行)
│   │   └── context_builder.py         ◀── events → LLM messages (590行)
│   │
│   ├── sandbox/                       ◀── 沙箱层 (10 文件, ~1400行)
│   │   ├── nsjail.py                  ◀── NsjailConfig + NsjailExecutor (375行)
│   │   ├── runner.py                  ◀── nsjail 内工具调度器 (157行)
│   │   ├── sandbox.py                 ◀── Sandbox (ISandbox 实现) (183行)
│   │   ├── sandbox_manager.py         ◀── SandboxManager 沙箱池 (290行)
│   │   ├── tools/
│   │   │   ├── base.py                ◀── BaseTool/ToolDefinition/ToolResult (164行)
│   │   │   ├── factory.py             ◀── ToolFactory + DynamicTool (239行)
│   │   │   └── registry.py            ◀── ToolRegistry
│   │   └── channels/
│   │       ├── base.py                ◀── BaseChannel 抽象
│   │       ├── napcat.py              ◀── NapCat QQ 渠道 (WebSocket) (421行)
│   │       ├── console.py             ◀── 控制台渠道
│   │       └── router.py              ◀── ChannelRouter (78行)
│   │
│   ├── workspace/                     ◀── 工作区层 (3 文件, ~850行)
│   │   ├── models.py                  ◀── User/Session/Artifact/SessionStatus (101行)
│   │   ├── db.py                      ◀── WorkspaceDB (SQLite + WAL) (306行)
│   │   └── manager.py                 ◀── WorkspaceManager (455行)
│   │
│   ├── memory/                        ◀── 记忆层 (2 文件, ~240行)
│   │   ├── __init__.py                ◀── 模块导出 (18行)
│   │   └── hindsight_client.py        ◀── HindsightClient (225行)
│   │
│   ├── resources/                     ◀── 资源层 (3 文件, ~300行)
│   │   ├── credentials.py             ◀── CredentialManager
│   │   ├── model_client.py            ◀── ModelClient (统一 LLM 接口)
│   │   └── resource_pool.py           ◀── ResourcePool (退避链) (212行)
│   │
│   ├── session/                       ◀── 会话层 (3 文件, ~300行)
│   │   ├── models.py                  ◀── 请求/响应模型
│   │   ├── repositories.py            ◀── 仓库抽象
│   │   └── session_manager.py         ◀── TemporalSessionManager (234行)
│   │
│   ├── storage/                       ◀── 存储层 (7 文件, ~400行)
│   │   ├── protocols.py               ◀── 存储协议
│   │   ├── postgres.py                ◀── PostgreSQL 后端
│   │   ├── file.py                    ◀── 文件存储后端
│   │   ├── redis.py                   ◀── Redis 缓存
│   │   ├── _memory.py                 ◀── 内存存储 (dev/test)
│   │   └── container.py               ◀── 存储容器 (DI)
│   │
│   └── account/                       ◀── 账号层 (2 文件)
│       ├── models.py
│       └── account_service.py         ◀── 渠道 ID → 统一 account_id
│
├── test/                              ◀── 测试目录 (4 文件, 65 tests)
│   ├── test_nsjail.py                 ◀── 22 tests: config + runner + executor
│   ├── test_workspace.py              ◀── 23 tests: DB + manager + config
│   ├── test_hindsight.py              ◀── 20 tests: client + disabled + no-server
│   └── ws_client.py                   ◀── WebSocket 测试客户端
│
└── docs/version/v6/                   ◀── 版本文档 (8 文件)
    ├── 01-refactoring-plan.md         ◀── nsjail 重构方案
    ├── 02-evaluation.md               ◀── 安全评估
    ├── 03-summary.md                  ◀── nsjail 实施总结
    ├── 04-workspace-evaluation.md     ◀── 工作区评估
    ├── 05-workspace-summary.md        ◀── 工作区实施总结
    ├── 06-hindsight-evaluation.md     ◀── 记忆系统评估
    ├── 07-hindsight-summary.md        ◀── 记忆系统实施总结
    └── 08-architecture.md             ◀── 本文档
```

---

## 11. 模块详解

### 11.1 common/ — 共享基础设施

| 文件 | 行数 | 职责 |
|------|------|------|
| `types.py` | 293 | 3 个枚举 (EventType, ChannelType, StopReason, ErrorSeverity) + 6 个数据类 (Event, UnifiedMessage, ToolCall, ToolResult, ModelResponse, SessionMetadata) |
| `interfaces.py` | 288 | 5 个抽象基类 (ISession, IResources, ISandbox, IChannel, ITool) 定义所有可替换组件的契约 |
| `errors.py` | ~20 | 自定义异常类 (SandboxNotFoundError, ToolNotFoundError, ModelAPIError, ValidationError) |

### 11.2 orchestration/ — 编排层

| 文件 | 行数 | 职责 |
|------|------|------|
| `worker.py` | 340 | init_dependencies() — 初始化 10 个共享依赖; start_worker() — 连接到 Temporal, Worker 注册 1 Workflow + 11 Activities, 启动 NapCat 监听; handle_message() — 渠道消息回调, resolve 账号, 创建/复用 Workflow |
| `workflow.py` | 285 | OrchestrationWorkflow — Temporal Workflow, 确定性编排引擎。run() 主循环 → _process_turn() 7 步 agentic loop. Signals: new_message(), cancel_session(). Queries: get_events(), get_status() |

### 11.3 harness/ — 指挥层 (大脑)

| 文件 | 行数 | 职责 |
|------|------|------|
| `activities.py` | 430 | 11 个 Temporal Activities: build_context_activity / get_available_tools_activity / call_model_activity / execute_tool_activity / send_response_activity / get_tool_result_activity / prepare_workspace_activity / finalize_workspace_activity / recall_activity / retain_activity / reflect_activity |
| `context_builder.py` | 590 | HarnessContextBuilder — 事件 → LLM messages. 3 种渠道身份 + 风格引导 + 工具纪律 + 环境感知 + 记忆注入 + 上下文文件加载 + prompt 注入检测 |

### 11.4 sandbox/ — 沙箱层 (双手)

| 文件 | 行数 | 职责 |
|------|------|------|
| `nsjail.py` | 375 | NsjailConfig (16 个配置字段) + NsjailExecutor (异步子进程执行, JSON 解析, Redis 持久化) |
| `runner.py` | 157 | nsjail 内部工具调度器, 3 个内置工具 (calculator/web_search/file_read), stdout JSON 约定 |
| `sandbox.py` | 183 | Sandbox (ISandbox 实现), 工具注册表委托 + nsjail 执行委托 |
| `sandbox_manager.py` | 290 | SandboxManager 池管理, create_sandbox() / create_session_sandbox() / destroy_sandbox() / cleanup_idle_sandboxes() |
| `tools/base.py` | 164 | BaseTool ABC + ToolDefinition + ToolResult + ToolType 枚举 |
| `tools/factory.py` | 239 | ToolFactory + DynamicTool, 内置工具构建 (calculator/search/file_read) |
| `tools/registry.py` | ~60 | ToolRegistry: register/get/has/unregister/list_all/list_definitions |
| `channels/base.py` | ~50 | BaseChannel + ChannelMessage 辅助类 |
| `channels/napcat.py` | 421 | NapCatChannel — OneBot v11 WebSocket 协议, 支持 4 种 post_type, 发送间隔控制 |
| `channels/router.py` | 78 | ChannelRouter — channel_type → IChannel 路由表 |

### 11.5 workspace/ — 工作区层

| 文件 | 行数 | 职责 |
|------|------|------|
| `models.py` | 101 | SessionStatus 枚举 + User / Session / Artifact 数据类 |
| `db.py` | 306 | WorkspaceDB — SQLite WAL 模式, 3 张表 (users/sessions/artifacts), 外键级联删除, 复合索引 |
| `manager.py` | 455 | WorkspaceManager — 用户/会话/产出全生命周期, 11 个子目录创建, nsjail bind mount 生成, YAML 原子写入 |

### 11.6 memory/ — 记忆层 (v6 新增)

| 文件 | 行数 | 职责 |
|------|------|------|
| `hindsight_client.py` | 225 | HindsightClient — retain/recall/reflect 3 API, MemoryItem 数据类, 降级策略 |

### 11.7 resources/ — 资源层

| 文件 | 行数 | 职责 |
|------|------|------|
| `resource_pool.py` | 212 | ResourcePool — 模型注册/退避链/代理请求, 支持主备切换 |
| `credentials.py` | ~50 | CredentialManager — API Key 管理 + 临时 Token |
| `model_client.py` | ~60 | ModelClient — Anthropic/OpenAI 统一接口适配 |

### 11.8 其他层

| 模块 | 文件 | 职责 |
|------|------|------|
| `session/` | session_manager.py (234行) | TemporalSessionManager — ISession 实现, 通过 Workflow Query 读事件 |
| `storage/` | 7 文件 (~400行) | 多种存储后端: PostgreSQL / File / Redis / Memory, 协议抽象 |
| `account/` | account_service.py | 渠道 (sender_id+channel_type) → 统一 account_id |

---

## 12. 数据模型字典

### 12.1 核心类型流转

```
OneBot JSON (外部)
  │
  │ NapCatChannel.normalize_message()
  ▼
UnifiedMessage (内部统一格式)
  │  message_id, session_id, account_id, sender_id,
  │  channel_type (NAPCAT/WEB/CONSOLE), content, metadata
  │
  │ worker.py 解构为 dict
  ▼
dict (Temporal Workflow 入参)
  │  content, sender_id, channel_type, session_id, account_id,
  │  workspace_user_uuid, workspace_session_id
  │
  │ self._events.append({type: "USER_MESSAGE", ...})
  ▼
Event[] (Temporal 事件历史)
  │  event_id, session_id, timestamp, event_type, content, metadata
  │
  │ ContextBuilder.build()
  ▼
messages[] (LLM API 格式)
  │  [{"role":"system","content":"..."},
  │   {"role":"user","content":"..."},
  │   {"role":"assistant","content":"..."}, ...]
  │
  │ ResourcePool.generate()
  ▼
ModelResponse
  │  content, tool_calls[], stop_reason, usage
  │
  │ send_response_activity()
  ▼
UnifiedMessage → ChannelRouter → NapCatChannel → OneBot API → QQ
```

### 12.2 枚举类型

| 枚举 | 值 | 用途 |
|------|-----|------|
| `EventType` | user_message, model_message, tool_call, tool_result, error, config_change, session_start, session_complete, session_archived, loop_started, loop_completed, turn_completed | 事件类型标识 |
| `ChannelType` | napcat, web, console | 消息渠道 |
| `StopReason` | end_turn, tool_use, max_tokens, refusal, error | 模型停止原因 |
| `ErrorSeverity` | recoverable, fatal | 错误严重级别 |
| `SessionStatus` | active, completed, failed, deleted | 工作区会话状态 |
| `ToolType` | native, mcp, skill | 工具来源 |

---

## 13. 配置参考

```yaml
# config/config.yaml

# ── 模型配置 (必填) ──
model:
  api_key: "sk-xxx"                          # LLM API 密钥
  base_url: "https://api.anthropic.com/v1"   # API 端点
  model: "claude-sonnet-4-6"                 # 模型名

# ── 应用配置 ──
app:
  max_history_turns: 10                       # 上下文窗口最大轮数
  max_turns: 20                              # 单次 agentic loop 最大工具轮数
  temporal_host: "localhost:7233"            # Temporal Server 地址

# ── Redis (可选, 用于工具结果持久化) ──
redis_url: ""                                # redis://host:6379

# ── nsjail 沙箱配置 ──
nsjail_binary: "/usr/bin/nsjail"
sandbox_chroot: "/"
sandbox_work_dir: "/work"
sandbox_runner: "/work/runner.py"
sandbox_python: "/usr/bin/python3"
sandbox_timeout: 30                          # 秒
sandbox_memory_mb: 256
sandbox_cpu_seconds: 10
sandbox_max_procs: 32
sandbox_max_files: 64
sandbox_disable_proc: true
sandbox_disable_network: true
sandbox_readonly_root: true

# ── 工作区配置 ──
workspace_root: "users_workspace"            # 工作区根目录
workspace_db: ""                             # SQLite 路径, 空=root/workspace.db

# ── Hindsight 记忆系统 (v6 新增) ──
hindsight:
  base_url: "http://hindsight:8000"          # Hindsight 服务地址
  api_key: ""                                 # API 密钥 (可选)
  timeout: 30.0                               # 请求超时 (秒)
  enabled: true                               # 是否启用记忆功能
```

---

## 14. 接口抽象层

### 14.1 ISession (会话记忆)

```
create_session(metadata) → session_id
emit_event(event)         → event_id
get_events(session_id, offset, limit, event_types) → Event[]
rewind_session(session_id, target_event_id) → {removed_count}
archive_session(session_id) → bool
list_sessions(limit, offset, status, tags) → SessionMetadata[]

实现: TemporalSessionManager (当前) / 未来可切换 File/PostgreSQL
```

### 14.2 IResources (模型资源)

```
initialize_models() → None
register_model(model_id, client, priority) → None
configure_fallback(group_name, primary, *fallbacks) → None
generate(messages, tools, stream) → ModelResponse
get_credential(resource_id, scope) → token
proxy_request(url, method, resource_id, headers, body) → response

实现: ResourcePool (含退避链)
```

### 14.3 ISandbox (工具沙箱)

```
execute(tool_name, arguments) → ToolResult
list_tools() → OpenAI-format definitions[]
health_check() → bool

实现: Sandbox → NsjailExecutor → nsjail subprocess → runner.py
```

### 14.4 IChannel (消息渠道)

```
normalize_message(raw) → UnifiedMessage
send_message(message)   → bool
start_monitor(callback) → bool
stop_monitor()          → bool

实现: NapCatChannel (WebSocket) / ConsoleChannel / WebChannel
```

### 14.5 ITool (工具定义)

```
name: str (property)
description: str (property)
parameters: JSON Schema (property)
execute(**kwargs) → ToolResult

实现: BaseTool → DynamicTool / 自定义子类
```

---

## 15. 测试覆盖

```
test/
├── test_nsjail.py          22 tests    沙箱隔离执行
│   ├── TestNsjailConfig      6 tests   nsjail 命令构建
│   ├── TestRunnerDirect      9 tests   runner.py 直接测试
│   └── TestNsjailExecutor    6 tests   nsjail 子进程执行
│
├── test_workspace.py        23 tests    多用户工作区
│   ├── TestWorkspaceDB       8 tests   SQLite CRUD + 级联删除
│   ├── TestWorkspaceManager 14 tests   目录创建/会话生命周期/nsjail集成
│   └── TestWorkspaceConfig   1 test    配置
│
├── test_hindsight.py        20 tests    Hindsight 记忆客户端
│   ├── TestMemoryItem        4 tests   数据类
│   ├── TestHindsightClientConfig 5   初始化与配置
│   ├── TestHindsightClientDisabled 4 降级模式
│   ├── TestRecallFormatted   2 tests   格式化输出
│   ├── TestHindsightClientNoServer 4 服务不可用降级
│   └── TestMemoryItemSorting 1 test    相关性排序
│
└── ws_client.py              测试辅助  WebSocket 客户端

Total: 65 tests, all passing
```

### 15.1 未覆盖区域

| 模块 | 原因 |
|------|------|
| worker.py | 需要 Temporal Server + NapCat 环境 |
| workflow.py | 需要 Temporal Server 环境 |
| channels/napcat.py | 需要真实 NapCat 客户端 |
| channels/router.py | 需要渠道实例 |
| context_builder.py | 需要真实 .hermes.md 文件环境 |
| resources/resource_pool.py | 需要真实模型 API 端点 |

---

## 附录 A: 关键设计决策速查

| 决策 | 选择 | 原因 |
|------|------|------|
| 编排引擎 | Temporal | 长期运行 Workflow + 崩溃恢复 + Signal/Query |
| 工具执行隔离 | nsjail | OS 级 PID/NET/FS/RLIMIT 隔离 |
| 工具执行格式 | runner.py stdout JSON | 与 nsjail 子进程通信的最简方式 |
| 工作区元数据 | SQLite + WAL | MVP 零运维, 未来可迁移 PostgreSQL |
| 工作区文件 | 本地文件系统 | 与 nsjail --bindmount 直接对接 |
| 记忆系统 | Hindsight (独立服务) | 专门优化的记忆检索/推理/存储 |
| Embedding 模型 | BAAI/bge-m3 | 中文优化, 1024 维向量 |
| Reranker 模型 | BAAI/bge-reranker-v2-m3 | 中文优化, 精排 |
| LLM 接口 | OpenAI 兼容格式 | 最大兼容性 (Anthropic/OpenAI/MiniMax) |
| 渠道协议 | OneBot v11 | QQ 机器人标准协议 |
| 渠道通信 | WebSocket (正向) | NapCat 连接 HpAgent, 主动推送 |
| Docker 运行时 | sysbox-runc (hpagent) | 容器内运行 nsjail |

## 附录 B: Activity 速查表

| # | Activity 名称 | 输入 | 输出 | 超时 |
|---|--------------|------|------|------|
| 1 | `build_context_activity` | events[], channel_type, recalled_memories | messages[] | 10s |
| 2 | `get_available_tools_activity` | — | tool_definitions[] | 10s |
| 3 | `call_model_activity` | context[], tools[] | model_response | 60s |
| 4 | `execute_tool_activity` | tool_name, arguments | tool_result | 30s |
| 5 | `send_response_activity` | content, user_message | bool | 10s |
| 6 | `get_tool_result_activity` | execution_id | cached_result | 10s |
| 7 | `prepare_workspace_activity` | user_uuid, session_id | workspace_ready | 30s |
| 8 | `finalize_workspace_activity` | session_id, status | artifacts_count | 30s |
| 9 | `recall_activity` | query, user_id, session_id | memories[] | 10s |
| 10 | `retain_activity` | events[], user_id, session_id | stored_count | 30s |
| 11 | `reflect_activity` | user_id | insights_count | 60s |
