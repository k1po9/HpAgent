# Worker 启动流程架构重设计

## 1. 问题诊断：当前 `worker.py` 无法运行

`start_worker()` 和 `init_dependencies()` 中存在 **3 个会导致 `NameError` 运行时崩溃的 bug**，说明当前代码是重构过程中的中间态，未经过端到端验证。

### Bug 1: SandboxManager 构造时引用了尚未赋值的变量

**位置**: `worker.py:195-203` vs `worker.py:252`

```python
# 第 195-203 行 —— mcp_mgr / skill_definitions / retriever 此刻不存在！
sandbox_manager = SandboxManager(
    ...
    mcp_manager=mcp_mgr,                # ❌ NameError
    skill_definitions=skill_definitions, # ❌ NameError
    retriever=retriever,                # ❌ NameError
)

# ... 50 行之后 ...

# 第 252 行 —— 赋值发生在这里，太晚了
mcp_mgr, skill_definitions, retriever = await setup_tools(config)
```

`setup_tools()` 的返回值在第 252 行才被赋值，但 `SandboxManager` 在第 195 行就已经使用了这些变量名。Python 解释器在函数入口处扫描到第 252 行有赋值语句，会将 `mcp_mgr`、`skill_definitions`、`retriever` 标记为函数局部变量，因此第 200-202 行引用它们时会抛出 `UnboundLocalError`。

### Bug 2: `workspace_root` 在 `start_worker()` 作用域中不存在

**位置**: `worker.py:352`

`workspace_root` 是 `init_dependencies()` 内部的局部变量（第 191 行），`start_worker()` 的 `handle_message` 闭包在第 352 行直接使用它：

```python
workspace_path = str(workspace_root / account_id / "sessions" / session_id / "workspace")
```

### Bug 3: `mcp_mgr` 在 cleanup 代码中不存在

**位置**: `worker.py:407`

```python
if mcp_mgr is not None:       # ❌ NameError
    await mcp_mgr.disconnect()
```

`mcp_mgr` 同样是 `init_dependencies()` 的局部变量，未返回给调用方。

---

## 2. 模块清单：当前各模块实际对外接口

### 2.1 已稳定模块（接口清晰，可直接使用）

| 模块 | 对外接口 | 职责 |
|------|----------|------|
| `common/types.py` | `Event`, `EventType`, `ChannelType`, `StopReason`, `UnifiedMessage`, `ToolCall`, `ToolResult`, `ModelResponse` | 全系统共享数据类型 |
| `common/interfaces.py` | `IResources`, `ISandbox`, `IChannel` | 抽象接口（ABC） |
| `common/errors.py` | `AgentError`, `ModelAPIError`, `SandboxNotFoundError`, `ToolExecutionError` 等 | 统一错误体系 |
| `common/logging.py` | `setup_logging()` | 日志初始化 |
| `orchestration/config.py` | `AppConfig` + 11 个子 dataclass | 全量配置加载（YAML → 强类型） |
| `orchestration/workflow.py` | `OrchestrationWorkflow`, `ReflectWorkflow`, `MetricsReportWorkflow` | Temporal Workflow 定义 |
| `resources/credentials.py` | `CredentialManager`, `ModelEndpoint` | API 密钥管理 + 端点注册 |
| `resources/model_client.py` | `ModelClient` | 单模型 HTTP 客户端（Anthropic/OpenAI） |
| `resources/resource_pool.py` | `ResourcePool` | 多模型注册 + 退避链 |
| `resources/embedding.py` | `create_embedding_client()` | Embedding 客户端工厂 |
| `storage/protocols.py` | `KeyValueStore`, `FileStore`, `Record`, `StoreError` | 存储层协议定义 |
| `storage/redis.py` | `RedisCache`, `RedisPubSub` | Redis 缓存实现 |
| `storage/file_store.py` | `LocalFileStore` | 本地文件存储实现 |
| `sandbox/sandbox.py` | `Sandbox` | 单沙箱：workspace 绑定 + ToolRegistry + 类别路由执行 |
| `sandbox/sandbox_manager.py` | `SandboxManager` | 沙箱池：按 session 创建/查询/销毁/空闲回收 |
| `sandbox/nsjail.py` | `NsjailConfig`, `NsjailExecutor` | nsjail 加固层（仅 Bash 工具） |
| `sandbox/tools/registry.py` | `ToolRegistry` | 三槽位工具注册中心（native/mcp/skill）+ RAG 检索 |
| `sandbox/tools/local/` | `LOCAL_TOOL_FACTORIES` | 6 个 workspace 绑定本地工具工厂 |
| `sandbox/tools/types.py` | `ToolResult` | 工具执行返回值 |
| `sandbox/tools/retriever.py` | `ToolVectorStore`, `ToolRetriever` | 工具 RAG 语义检索 |
| `sandbox/tools/adapters/mcp.py` | `MCPToolManager` | MCP 协议工具适配器 |
| `sandbox/tools/skills/engine.py` | `SkillPipeline`, `build_skill_tool()` | Skills 流水线引擎 |
| `sandbox/channels/base.py` | `BaseChannel` | 渠道基类（实现 IChannel） |
| `sandbox/channels/napcat.py` | `NapCatChannel` | QQ/NapCat 渠道 |
| `sandbox/channels/console.py` | `ConsoleChannel` | 控制台交互渠道 |
| `sandbox/channels/router.py` | `ChannelRouter` | 按 channel_type 路由到对应 IChannel |
| `session/models.py` | `Session`, `SessionStatus` | 会话实体 |
| `session/db.py` | `WorkspaceDB` | SQLite 元数据存储（users + sessions 表） |
| `session/store.py` | `SessionStore` | 会话存储：Redis 热数据 + Hindsight 长期记忆 + 本地备份 |
| `session/workspace.py` | `init_user()`, `init_session()` | 工作区目录初始化 |
| `harness/runner.py` | `HarnessRunner` | 无状态协调器：聚合记忆/上下文/模型/工具/渠道 |
| `harness/context_builder.py` | `HarnessContextBuilder` | 事件历史 → LLM messages 转换 + 渠道感知 prompt 拼接 |
| `harness/prompts.py` | `PromptLoader` | YAML prompt 加载器 |
| `harness/activities.py` | `inject()` + 5 个 `@activity.defn` | Temporal Activity 薄封装层 |
| `account/account_service.py` | `AccountService` | 渠道身份 → 统一账号解析（JSON 文件持久化） |
| `memory/hindsight_client.py` | `HindsightClient`, `MemoryItem`, `HindsightMetrics` | Hindsight 记忆服务 REST 客户端 |
| `agent/runner.py` | `MultiAgentExecutor` | 多 Agent 编排执行器（仅在 mode=multi 时使用） |

### 2.2 已删除但 worker.py 仍在引用的模块

| 引用 | 状态 |
|------|------|
| `from sandbox.runner import ...` | 文件已删除 (`D src/sandbox/runner.py`) |
| `from sandbox.tools.base import ...` | 文件已删除 (`D src/sandbox/tools/base.py`) |
| `from sandbox.tools.factory import ...` | 文件已删除 (`D src/sandbox/tools/factory.py`) |
| `from workspace import ...` | 整个包已删除 (`D src/workspace/`) |

worker.py 当前没有直接 import 这些已删除文件，但 `sandbox/tools/__init__.py` 的 docstring 仍引用了不存在的 `calculator_tool`、`file_read_tool`、`web_search_tool`。

---

## 3. 正确的依赖初始化顺序

依赖之间存在有向无环图（DAG）关系，初始化必须按拓扑顺序进行：

```
                        config.yaml + models.yaml
                               │
                        AppConfig.from_yaml()
                               │
              ┌────────────────┼──────────────────┐
              ▼                ▼                    ▼
     CredentialManager    setup_tools()      NsjailConfig
              │           (MCP+Skills+RAG)        │
              ▼                │                   │
       ResourcePool            │                   │
              │                │                   │
              │                ▼                   │
              │     (mcp_mgr, skills, retriever)   │
              │                │                   │
              ├────────────────┼───────────────────┤
              │                ▼                   │
              │        SandboxManager              │
              │                │                   │
              ▼                │                   │
        HindsightClient        │                   │
              │                │                   │
              ▼                │                   │
         SessionStore          │                   │
              │                │                   │
              ▼                │                   │
     HarnessContextBuilder     │                   │
              │                │                   │
              ├────────────────┴───────────────────┤
              │                                    │
              ▼                                    │
        HarnessRunner ◄────────────────────────────┘
```

关键规则：
1. **`setup_tools()` 必须在 `SandboxManager` 构造之前调用**
2. **`CredentialManager` → `ResourcePool` 是不可逆的线性依赖**
3. **`SandboxManager` 需要 `NsjailConfig` + `setup_tools()` 产物 + `data_root`**
4. **`SessionStore` 需要 `RedisCache` (可选) + `HindsightClient` (可选) + `LocalFileStore` (可选)**
5. **`HarnessRunner` 需要所有上述组件的实例**

---

## 4. 重设计后的模块交互图

```
                            ┌─────────────────────────────┐
                            │       Temporal Server         │
                            │   Workflows + Activities      │
                            └──────────────┬──────────────┘
                                           │
                              ┌────────────┴────────────┐
                              │     Temporal Worker      │
                              │  ┌─────────────────────┐ │
                              │  │ OrchestrationWorkflow│ │
                              │  │ ReflectWorkflow      │ │
                              │  │ MetricsReportWorkflow│ │
                              │  └─────────┬───────────┘ │
                              │            │              │
                              │  ┌─────────▼───────────┐ │
                              │  │  Activities (薄封装)  │ │
                              │  │  process_turn        │ │
                              │  │  archive_session     │ │
                              │  │  reflect / metrics   │ │
                              │  └─────────┬───────────┘ │
                              └────────────┼─────────────┘
                                           │ inject()
                              ┌────────────▼────────────┐
                              │     HarnessRunner        │
                              │  (无状态协调器)           │
                              │                          │
                              │  process_turn() 循环:     │
                              │    recall → context      │
                              │    → model → tools       │
                              │    → response → retain   │
                              └──┬───┬─────┬──────┬─────┘
                                 │   │     │      │
              ┌──────────────────┘   │     │      └──────────────┐
              ▼                      ▼     ▼                     ▼
     ┌──────────────┐   ┌──────────────┐  ┌──────────────┐  ┌──────────┐
     │ SessionStore  │   │ ContextBuilder│  │ ResourcePool │  │ Sandbox  │
     │               │   │              │  │              │  │ Manager  │
     │ Redis 热数据   │   │ prompt 拼接   │  │ 模型退避链    │  │          │
     │ Hindsight 记忆 │   │ 渠道感知身份   │  │ API 调用     │  │ per-session
     │ 本地文件备份    │   │ 项目上下文注入 │  │              │  │ 沙箱池   │
     └──────┬────────┘   └──────────────┘  └──────────────┘  └────┬─────┘
            │                                                      │
            ▼                                                      ▼
   ┌────────────────┐                                   ┌──────────────────┐
   │ HindsightClient │                                   │ ChannelRouter    │
   │ (REST API)     │                                   │                  │
   └────────────────┘                                   │ NapCatChannel    │
                                                        │ ConsoleChannel   │
                                                        │ (+ Web future)   │
                                                        └──────────────────┘
```

---

## 5. `init_dependencies()` 重构方案

### 5.1 返回值扩展

当前返回值缺少 `workspace_root` 和 `mcp_mgr`，需要扩展：

```python
async def init_dependencies(config: AppConfig) -> WorkerDependencies:
    """..."""
    # 返回一个 dataclass 而非裸 tuple，避免位置耦合
    ...
```

### 5.2 初始化顺序修正

```
 1. CredentialManager + ResourcePool
 2. Redis (可选)
 3. NsjailConfig (纯数据，无 IO)
 4. setup_tools() → mcp_mgr, skill_definitions, retriever  ← 移到这里
 5. workspace_root + LocalFileStore + WorkspaceDB
 6. SandboxManager(..., mcp_manager=mcp_mgr, skill_definitions=..., retriever=...)  ← 现在可用
 7. PromptLoader
 8. HindsightClient (可选)
 9. HarnessContextBuilder (enable_context_files=True, enable_tool_guidance=True)
10. AccountService
11. ChannelRouter
12. SessionStore
13. MultiAgentExecutor (条件)
14. HarnessRunner
```

### 5.3 引入 `WorkerDependencies` dataclass

```python
@dataclass
class WorkerDependencies:
    """init_dependencies() 的返回值，包含所有组装好的共享依赖。"""
    harness_runner: HarnessRunner
    account_service: AccountService
    channel_router: ChannelRouter
    sandbox_manager: SandboxManager
    file_store: LocalFileStore
    workspace_db: WorkspaceDB
    workspace_root: Path
    mcp_manager: Any              # MCPToolManager | None，用于 shutdown cleanup
    resource_pool: ResourcePool   # 用于可能的直接访问
```

---

## 6. `start_worker()` 重构方案

### 6.1 完整流程

```
start_worker(config)
  │
  ├─ 1. deps = await init_dependencies(config)
  │      └─ 按 5.2 的正确顺序初始化所有依赖
  │
  ├─ 2. inject(harness=deps.harness_runner)
  │      └─ 注入到 activities 模块的模块级 _harness 变量
  │
  ├─ 3. client = await Client.connect(config.temporal.host)
  │
  ├─ 4. worker = Worker(client, ...)
  │      └─ 注册 3 个 Workflow + 5 个 Activity
  │
  ├─ 5. 渠道注册
  │      ├─ deps.channel_router.register(NAPCAT, NapCatChannel())
  │      └─ deps.channel_router.register(CONSOLE, ConsoleChannel())  ← 新增
  │
  ├─ 6. handle_message 闭包
  │      ├─ account_service.resolve() → account_id
  │      ├─ init_user() + init_session() (幂等工作区初始化)
  │      ├─ sandbox_manager.create_session_sandbox() (幂等沙箱创建)
  │      └─ Temporal workflow start/signal
  │
  ├─ 7. async with worker:
  │      ├─ napcat.start_monitor(handle_message)
  │      ├─ _setup_reflect_schedule(client, ...)
  │      ├─ _setup_metrics_schedule(client, ...)
  │      ├─ _start_sandbox_cleanup_task(sandbox_manager, interval=300)  ← 新增
  │      └─ await asyncio.Future()  # 永久等待
  │
  └─ 8. Shutdown cleanup
         ├─ napcat.stop_monitor()               ← 新增
         ├─ deps.mcp_manager.disconnect()        ← 修复作用域
         └─ sandbox_manager.cleanup_all()        ← 新增
```

### 6.2 `handle_message` 闭包修正

```python
async def handle_message(message: UnifiedMessage) -> None:
    if not message.content or not message.content.strip():
        return

    ch_type = (
        message.channel_type.value
        if hasattr(message.channel_type, "value")
        else str(message.channel_type)
    )

    account_id = await deps.account_service.resolve(ch_type, message.sender_id)
    session_id = f"session-{account_id}"

    # 使用 deps.workspace_root 而非未定义的 workspace_root
    workspace_path = str(
        deps.workspace_root / account_id / "sessions" / session_id / "workspace"
    )

    # ... rest
```

### 6.3 闲置沙箱清理后台任务

```python
async def _start_sandbox_cleanup_task(sandbox_manager, interval: int = 300):
    """每 interval 秒清理一次闲置沙箱。"""
    import asyncio
    while True:
        try:
            await asyncio.sleep(interval)
            cleaned = sandbox_manager.cleanup_idle_sandboxes()
            if cleaned > 0:
                logger.info("Sandbox cleanup: %d idle sandboxes destroyed", cleaned)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning("Sandbox cleanup error: %s", e)
```

---

## 7. HarnessContextBuilder 配置修正

当前 `worker.py:227` 创建时未启用关键功能：

```python
# 当前 —— 上下文文件加载和工具纪律永不被激活
context_builder = HarnessContextBuilder(prompt_loader=prompt_loader)

# 应改为 ——
context_builder = HarnessContextBuilder(
    prompt_loader=prompt_loader,
    enable_context_files=True,     # 激活 .hermes.md / CLAUDE.md 加载
    enable_tool_guidance=True,     # 激活工具使用纪律提示
)
```

同时 `enable_context_files` 的默认值应从 `False` 改为 `True`，因为这个功能是整个 `context_builder.py` 中 ~200 行代码存在的理由。

---

## 8. 渠道注册完整性

当前只注册了 NapCat：

```python
# 当前 —— 只注册了一个渠道
napcat = NapCatChannel()
channel_router.register(ChannelType.NAPCAT, napcat)

# 应改为 —— 注册所有可用渠道
channel_router.register(ChannelType.NAPCAT, NapCatChannel())
channel_router.register(ChannelType.CONSOLE, ConsoleChannel())
# channel_router.register(ChannelType.WEB, WebChannel())  # 待实现
```

NapCat 实例需要提前创建（因为后续要调用 `start_monitor` 和 `stop_monitor`），Console 则不需要。

---

## 9. 修改清单

| # | 文件 | 修改内容 | 优先级 |
|---|------|----------|--------|
| 1 | `worker.py:195-252` | 将 `setup_tools()` 调用移到 `SandboxManager` 构造之前 | **P0 - 崩溃** |
| 2 | `worker.py:352` | 将 `workspace_root` 替换为从 `init_dependencies()` 获取的值 | **P0 - 崩溃** |
| 3 | `worker.py:407` | 将 `mcp_mgr` 替换为从 `init_dependencies()` 获取的值 | **P0 - 崩溃** |
| 4 | `worker.py` | 引入 `WorkerDependencies` dataclass 替代裸 tuple 返回 | P1 - 架构 |
| 5 | `worker.py:227` | `HarnessContextBuilder` 启用 `enable_context_files=True`, `enable_tool_guidance=True` | P1 - 功能缺失 |
| 6 | `worker.py:325` | 注册 `ConsoleChannel` | P2 - 功能缺失 |
| 7 | `worker.py` | 添加后台沙箱清理任务 `_start_sandbox_cleanup_task()` | P2 - 资源泄漏 |
| 8 | `worker.py:404-412` | 添加 NapCat `stop_monitor()` + 完整 shutdown cleanup | P2 - 优雅关闭 |
| 9 | `context_builder.py:280` | `enable_context_files` 默认值从 `False` 改为 `True` | P3 - 默认值 |
