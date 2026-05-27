# 工具体系：Agent 的 Tool / Skill / MCP 执行层

> 从 harness (gitness) Go 源码中提取的架构模式，用 Python 重新表述，可直接迁移到目标项目。
> 涵盖：工具注册、隔离执行、LLM prompt 注入、插件解析、生命周期管理。

---

## 目录

1. [harness 中没有显式的 "tool 层"](#harness-中没有显式的-tool-层)
2. [从三条执行路径提取的架构模式](#从三条执行路径提取的架构模式)
3. [统一工具体系设计](#统一工具体系设计)
4. [核心接口与数据结构](#核心接口与数据结构)
5. [ToolRegistry：注册与管理](#toolregistry注册与管理)
6. [ToolExecutor：隔离执行](#toolexecutor隔离执行)
7. [ToolFactory & Plugin Resolution](#toolfactory--plugin-resolution)
8. [LLM Prompt 注入机制](#llm-prompt-注入机制)
9. [生命周期管理](#生命周期管理)
10. [Skill 与 MCP 的设计](#skill-与-mcp-的设计)
11. [完整目录结构](#完整目录结构)

---

## harness 中没有显式的 "tool 层"

harness (gitness) 是一个 Git 托管 + CI/CD 平台，**没有为 LLM Agent 设计的显式工具体系**。但它有三条执行路径，每条都贡献了可以直接迁移到工具体系设计的模式：

| 执行路径 | 源文件 | 做什么 | 对工具层的价值 |
|---------|-------|-------|-------------|
| **Pipeline 系统** | `app/pipeline/runner/` `app/pipeline/manager/` | YAML 编译为 Docker 容器执行 | 步骤隔离、插件解析、生命周期回调 |
| **Job 系统** | `job/executor.go` `job/scheduler.go` | 后台任务注册 + 异步执行 | Handler 注册表、panic 恢复、ProgressReporter |
| **AI Task 系统** | `app/events/aitask/` `app/services/aitaskevent/` | 事件驱动的 AI agent 执行 | tool 触发方式、状态机、prompt 传递 |

下面逐条解剖。

---

## 从三条执行路径提取的架构模式

### 路径一：Pipeline —— 步骤隔离与插件解析

```
Pipeline YAML
    │
    ▼
Converter (Jsonnet/Starlark → YAML)          ← 输入可以是模板
    │
    ▼
Triggerer (parse, build DAG, schedule)        ← 编译为可执行计划
    │
    ▼
Runner (compile → Docker containers)          ← 隔离执行环境
    │
    ▼
Manager (BeforeStage → BeforeStep → AfterStep → AfterStage)  ← 生命周期回调
```

**提取的接口模式**（Go 源码 → Python 映射）：

```python
# harness: types/step.go Step struct
# → 工具执行的原子单元
@dataclass
class ToolStep:
    """一次工具调用的执行记录。"""
    id: int
    name: str                          # 工具名，如 "calculator"
    status: ToolStepStatus             # pending / running / success / failure
    input: dict                        # 调用参数（对应 LLM tool_call arguments）
    output: Any | None                 # 执行结果
    error: str | None                  # 失败原因
    exit_code: int                     # 执行退出码
    started: int                       # 开始时间戳
    stopped: int                       # 结束时间戳

# harness: app/pipeline/manager/manager.go ExecutionManager interface
# → 工具执行的生命周期管理接口
class ToolExecutionManager(Protocol):
    """Runner 向上层报告进度的回调接口。"""

    async def before_step(self, step: ToolStep) -> None:
        """步骤开始前：创建日志流、更新状态。"""
        ...

    async def after_step(self, step: ToolStep) -> None:
        """步骤结束后：持久化结果、触发下游。"""
        ...

    async def write_log(self, step_id: int, line: str) -> None:
        """流式写入日志。"""
        ...
```

**插件解析模式**（harness: `app/pipeline/resolver/resolve.go`）：

```
Pipeline YAML 中引用 plugin "my-plugin@v1"
    │
    ▼
resolver.Resolve(name="my-plugin", version="v1")
    │
    ▼
pluginStore.Find(name, version)  →  Plugin{Spec: "<YAML template>"}
    │
    ▼
parse v1yaml.Config from Spec  →  替换到 pipeline 中的 plugin 引用
```

→ 工具的 **plugin 模式**：工具定义可以存在 DB/配置文件中，运行时按名解析。

### 路径二：Job —— 注册表与异常恢复

```
scheduler.RunJob(definition)
    │
    ▼
store.Create(job) → store.ListReady() → preExec()
    │
    ▼
executor.exec(jobType, input)
    │  recover()        ← panic 永不泄漏
    │  handlerMap[jobType].Handle(ctx, input, progressReporter)
    ▼
postExec() → store.UpdateExecution() → pubsub 通知
```

**提取的核心模式**（harness: `job/executor.go`）：

```python
# harness: job/executor.go Handler interface
# → 工具的通用执行接口
class ToolHandler(Protocol):
    """单个工具的执行逻辑。"""

    async def handle(
        self,
        ctx: ExecutionContext,
        input: dict,                 # LLM 传入的 arguments
        on_progress: Callable[[int, str], Awaitable[None]],
    ) -> ToolResult: ...

# harness: job/executor.go Executor struct
# → 工具注册表 + 执行调度
class ToolRegistry:
    """线程安全的工具注册与执行容器。

    关键设计（均来自 harness Go）：
    - map[string]Handler 索引（harness: executor.handlerMap）
    - Register() 单线程注册，完成后 freeze（harness: finishRegistration）
    - exec() 内 recover() 捕获 panic（harness: executor.exec:103-109）
    - ProgressReporter 回调（harness: executor.exec:119-147）
    """

    def __init__(self):
        self._tools: dict[str, ToolHandler] = {}
        self._frozen: bool = False
        self._lock = threading.RLock()

    def register(self, name: str, handler: ToolHandler) -> None:
        if self._frozen:
            raise RegistryFrozenError("tool registration is frozen")
        with self._lock:
            self._tools[name] = handler

    def freeze(self) -> None:
        """封禁注册，防止运行时注入未审计工具。

        对应 harness: executor.finishRegistration() (job/executor.go:90)
        """
        self._frozen = True

    async def execute(self, tool_name: str, input: dict) -> ToolResult:
        """执行工具，异常安全。

        对应 harness: executor.exec() 中的 recover() (job/executor.go:103-109)
        """
        handler = self._tools.get(tool_name)
        if handler is None:
            return ToolResult(success=False, error=f"tool '{tool_name}' not registered")

        try:
            return await handler.handle(ExecutionContext(), input, self._progress)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

### 路径三：AI Task —— 事件驱动的工具触发

```
外部写入 AITask 记录（含 InitialPrompt）
    │
    ▼
事件系统发出 AITaskEventStart
    │
    ▼
aitaskevent.Service.handleAITaskEvent()
    │
    ▼
或从 DB 加载 AITask → 验证 gitspace 状态 → Orchestrator.TriggerAITask()
    │
    ▼
Container Orchestrator.StartAITask(gitspaceConfig, infra, aiTask)
    │  aiTask.InitialPrompt  ↓  传递给容器内的 Claude Code
    ▼
容器内: claude --prompt "<InitialPrompt>" --output-format json
```

**提取的 prompt 传递与状态机**：

```python
# harness: types/ai_task.go AITask struct
# → 一次 agent 工具调用的完整记录
@dataclass
class AgentTask:
    """一次 agent 工具调用的持久化记录。"""
    identifier: str                   # 唯一标识
    initial_prompt: str               # LLM 传入的 prompt / tool input
    agent_type: AgentType             # 执行代理类型
    state: TaskState                  # uninitialized → running → completed / error
    output: str | None                # 执行结果
    error_message: str | None         # 失败信息
    usage_metric: UsageMetric | None  # token 消耗统计

# harness: types/enum/ai_task_state.go
# → 任务状态机
class TaskState(StrEnum):
    UNINITIALIZED = "uninitialized"
    RUNNING = "running"
    COMPLETED = "completed"
    ERROR = "error"

    def is_final(self) -> bool:
        return self in (TaskState.COMPLETED, TaskState.ERROR)

# harness: app/services/aitaskevent/handler.go
# → 事件驱动的工具执行入口
class AITaskEventHandler:
    """监听 AITask 事件并调度执行。

    对应 harness 的 aitaskevent.Service + handler.go
    """

    def __init__(self, store: AITaskStore, orchestrator: ToolOrchestrator):
        self._store = store
        self._orchestrator = orchestrator

    async def handle_start_event(self, event: AITaskEvent) -> None:
        # 1. 从 DB 加载 task（含 initial_prompt），带重试
        ai_task = await self._load_with_retry(event.task_identifier, retries=3)

        # 2. 状态检查
        if ai_task.state != TaskState.UNINITIALIZED:
            return

        # 3. 执行
        try:
            await self._store.update_state(ai_task, TaskState.RUNNING)
            result = await self._orchestrator.execute(ai_task)
            await self._store.update(ai_task, state=TaskState.COMPLETED, output=result)
        except Exception as e:
            await self._store.update(ai_task, state=TaskState.ERROR, error=str(e))
```

### 四种模式的关系

```
                      ┌────────────────────────────┐
                      │   ToolOrchestrator (AI Task)│  ← 事件驱动调度
                      │   "哪个工具执行哪个任务"      │
                      └──────────┬─────────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              ▼                  ▼                  ▼
    ┌─────────────────┐ ┌──────────────┐ ┌─────────────────┐
    │  ToolRegistry    │ │ ToolExecutor │ │ ExecutionManager│
    │  (Job 模式)      │ │ (Pipeline)   │ │ (Pipeline)      │
    │                  │ │              │ │                 │
    │ register/freeze  │ │ isolate/exec │ │ before/after    │
    │ execute/recover  │ │ timeout/oom  │ │ log/progress    │
    └─────────────────┘ └──────────────┘ └─────────────────┘
```

---

## 统一工具体系设计

综合 harness 三条路径的模式，设计一个统一的工具体系：

```
                        LLM 返回 tool_call
                    {"name": "web_search", "arguments": {...}}
                              │
                              ▼
┌─ Agent Loop（编排层）────────────────────────────────────────────────┐
│  1. registry.list_for_llm() → OpenAI tool definitions              │
│  2. llm.generate(messages, tools=definitions)                      │
│  3. if response.tool_calls:                                        │
│       for tc in tool_calls:                                        │
│         result = orchestrator.execute(tc.name, tc.arguments)       │
│         追加 tool_result → 回到 step 2                              │
└─────────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─ ToolOrchestrator ───────────────────────────────────────────────────┐
│  - 按 tool_type 路由: NATIVE → InProcess / MCP → MCPClient          │
│  - 管理 ToolExecutionManager 生命周期回调                            │
└─────────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┼───────────────┐
              ▼               ▼               ▼
       InProcessExecutor  DockerExecutor  MCPExecutor
       (开发环境)          (生产隔离)      (外部工具)
```

### 三种工具类型

| 类型 | 来源 | 执行方式 | harness 对应模式 |
|------|------|---------|:---:|
| **Tool** (NATIVE) | 本地 Python 函数 | InProcess / Docker 容器 | Pipeline step |
| **Skill** | 多 Tool 组合编排 | ToolOrchestrator 递归执行 | Pipeline DAG |
| **MCP** | 外部 MCP Server | MCP Client 协议通信 | Plugin resolution |

---

## 核心接口与数据结构

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Callable, Protocol

# ═══════════════════════════════════════════════════════════════
# 类型定义
# ═══════════════════════════════════════════════════════════════

class ToolType(StrEnum):
    """工具来源类型。"""
    NATIVE = "native"      # 本地函数
    SKILL  = "skill"       # 多工具组合
    MCP    = "mcp"         # 外部 MCP Server


class ToolStepStatus(StrEnum):
    """harness: types/enum/ci_status.go CIStatus"""
    PENDING  = "pending"
    RUNNING  = "running"
    SUCCESS  = "success"
    FAILURE  = "failure"
    SKIPPED  = "skipped"
    ERROR    = "error"


@dataclass
class ToolResult:
    """统一返回值 —— harness: job/executor.go recover() 保证异常永不泄漏。"""
    success: bool
    output: Any = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)
    # 建议: execution_id, elapsed_ms, exit_code, tool_type


@dataclass
class ToolDefinition:
    """工具元信息 —— 与执行逻辑分离。LLM 只看到这部分。"""
    name: str
    description: str
    parameters: dict           # JSON Schema
    tool_type: ToolType
    metadata: dict = field(default_factory=dict)

    def to_openai_format(self) -> dict:
        """harness 中不存在此方法 —— 这是 LLM 集成特有的。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

# ═══════════════════════════════════════════════════════════════
# 核心抽象
# ═══════════════════════════════════════════════════════════════

class BaseTool(ABC):
    """工具抽象基类。

    设计来源:
    - harness: job/executor.go Handler interface
    - harness: app/gitspace/orchestrator/ide/ide.go IDE interface (多方法抽象)
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    @abstractmethod
    def parameters(self) -> dict: ...

    @property
    def tool_type(self) -> ToolType:
        return ToolType.NATIVE

    @abstractmethod
    async def execute(self, **kwargs) -> ToolResult: ...

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            tool_type=self.tool_type,
        )


class ExecutionContext:
    """执行上下文 —— harness: context.Context + deadline propagation."""
    def __init__(self, timeout_ms: int = 30000):
        self.deadline = time.time() + timeout_ms / 1000
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True

    def is_expired(self) -> bool:
        return time.time() > self.deadline
```

---

## ToolRegistry：注册与管理

直接对应 `job/executor.go` 的 `Executor` 结构：

```python
import threading
from typing import Protocol

class ProgressReporter(Protocol):
    """harness: job/executor.go ProgressReporter 函数类型。"""
    async def __call__(self, progress: int, message: str) -> None: ...


class ToolRegistry:
    """工具注册表 —— harness: job/executor.go Executor struct。

    关键设计:
    - map[string]Handler (executor.handlerMap)
    - finishRegistration() 封禁 (job/executor.go:90)
    - recover() 异常捕获 (job/executor.go:103)
    - 单线程注册，多线程执行
    """

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}
        self._frozen: bool = False
        self._lock = threading.RLock()

    # ── 注册 ────────────────────────────────────────

    def register(self, tool: BaseTool) -> None:
        """注册工具。

        对应 harness: executor.Register(jobType, Handler) (job/executor.go:68)
        设计决策同 harness: 单线程调用（启动阶段），检查空值和重复。
        """
        if self._frozen:
            raise RegistryFrozenError("registration is frozen")
        if not tool.name:
            raise ValueError("tool name must not be empty")
        with self._lock:
            if tool.name in self._tools:
                raise DuplicateToolError(f"tool '{tool.name}' already registered")
            self._tools[tool.name] = tool

    def freeze(self) -> None:
        """封禁注册 —— 防止运行时装入了未审计的工具。

        对应 harness: executor.finishRegistration() (job/executor.go:90)
        """
        self._frozen = True

    # ── 查询 ────────────────────────────────────────

    def get(self, name: str) -> BaseTool | None:
        with self._lock:
            return self._tools.get(name)

    def list_all(self) -> list[BaseTool]:
        with self._lock:
            return list(self._tools.values())

    def list_for_llm(self) -> list[dict]:
        """返回 OpenAI function calling 格式的工具列表。
        这就是"工具注入 prompt"的入口。
        """
        return [t.get_definition().to_openai_format() for t in self.list_all()]

    # ── 执行 ────────────────────────────────────────

    async def execute(
        self,
        tool_name: str,
        arguments: dict,
        on_progress: ProgressReporter | None = None,
    ) -> ToolResult:
        """执行工具，异常安全。

        对应 harness: executor.exec() (job/executor.go:98)
        - recover() → try/except
        - errNoHandlerDefined → ToolResult(success=False)
        """
        tool = self.get(tool_name)
        if tool is None:
            return ToolResult(success=False, error=f"tool '{tool_name}' not registered")

        try:
            result = await tool.execute(**arguments)
            if isinstance(result, ToolResult):
                return result
            return ToolResult(success=True, output=result)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

---

## ToolExecutor：隔离执行

harness Pipeline 的核心价值——步骤在 Docker 容器中隔离执行。工具体系同样需要可插拔的执行器：

```python
class ToolExecutor(Protocol):
    """工具执行器 —— 可插拔的隔离策略。

    对应 harness 的做法:
    - Pipeline:  Docker 容器隔离 (runner/runner.go)
    - AI Task:   容器内执行 Claude Code (gitspace orchestrator)
    - Job:       进程内 goroutine 执行 (job/executor.go)

    每种实现:
    - InProcessExecutor: 进程内执行（开发环境）
    - SubprocessExecutor: 子进程隔离
    - DockerExecutor: 容器隔离（生产环境，等价于 harness pipeline runner）
    - MCPExecutor: MCP 协议与外部服务通信
    """

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult: ...

    async def setup(self) -> None: ...

    async def teardown(self) -> None: ...


class InProcessExecutor:
    """进程内执行器 —— 等价于 harness 的 job executor。"""

    def __init__(self, registry: ToolRegistry):
        self._registry = registry

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        return await self._registry.execute(tool_name, arguments)

    async def setup(self) -> None: pass
    async def teardown(self) -> None: pass


class DockerExecutor:
    """Docker 容器隔离执行 —— 等价于 harness pipeline runner。

    对应 harness:
    - runner/runner.go: compiler + engine + execer
    - 每个 step 启动独立容器
    """

    def __init__(self, registry: ToolRegistry, image: str = "tool-runner:latest"):
        self._registry = registry
        self._image = image

    async def execute(self, tool_name: str, arguments: dict) -> ToolResult:
        """在独立容器中运行工具。

        对应 harness pipeline 的执行模型:
        - compiler 将 step 编译为容器配置
        - exec 启动容器并等待完成
        - 收集 stdout/stderr + exit_code

        安全隔离:
        - CPU / Memory 限制（harness: 不使用 rlimit，用 Docker 的 cgroup）
        - 网络限制（可选）
        - 文件系统隔离（volume mount）
        - 超时控制（context deadline）
        """
        container_config = {
            "Image": self._image,
            "Cmd": ["python", "-m", "tools.runner", tool_name, json.dumps(arguments)],
            "HostConfig": {
                "Memory": 256 * 1024 * 1024,     # 256MB
                "NanoCPUs": 1_000_000_000,       # 1 CPU
                "NetworkMode": "none",            # 无网络
            },
        }
        # ... Docker SDK 执行 + 结果解析
        return ToolResult(success=True, output={})
```

### 执行器切换（harness 的优雅降级）

```python
class ExecutorFactory:
    """按环境选择执行器 —— 对应 harness: container/orchestrator_factory.go。

    设计来源: harness 的 Factory 模式
    - container/orchestrator_factory.go: map[enum.InfraProviderType]Orchestrator
    - secret/resolver_factory.go:      map[enum.SecretType]Resolver
    """

    def __init__(self, config: AppConfig):
        self._config = config

    def build(self, registry: ToolRegistry) -> ToolExecutor:
        if self._config.executor == "docker":
            return DockerExecutor(registry)
        elif self._config.executor == "subprocess":
            return SubprocessExecutor(registry, timeout=30.0)
        else:
            # fallback: always available
            return InProcessExecutor(registry)
```

---

## ToolFactory & Plugin Resolution

结合 harness 的两种解析模式：
1. **内置工具**（`ai_agent.go` function map）
2. **插件解析**（`resolver/resolve.go` 从 DB 按名查找）

```python
# ═══════════════════════════════════════════════════════════
# 模式一: DynamicTool —— 对应 harness ai_agent.go function map
# ═══════════════════════════════════════════════════════════

class DynamicTool(BaseTool):
    """无需定义新类，通过函数引用构造。

    对应 harness: ai_agent.go
      var installationMap = map[enum.AIAgent]installAgentFun{
          enum.AIAgentClaudeCode: installClaudeCode,
      }
    """

    def __init__(
        self,
        name: str,
        description: str,
        parameters: dict,
        execute_func: Callable[..., Awaitable[ToolResult]],
        tool_type: ToolType = ToolType.NATIVE,
    ):
        self._name = name
        self._description = description
        self._parameters = parameters
        self._execute_func = execute_func
        self._tool_type = tool_type

    @property
    def name(self) -> str: return self._name

    @property
    def description(self) -> str: return self._description

    @property
    def parameters(self) -> dict: return self._parameters

    @property
    def tool_type(self) -> ToolType: return self._tool_type

    async def execute(self, **kwargs) -> ToolResult:
        return await self._execute_func(**kwargs)


# ═══════════════════════════════════════════════════════════
# 模式二: ToolStore + Plugin Resolution
# 对应 harness resolver.Resolve() + pluginStore.Find()
# ═══════════════════════════════════════════════════════════

class ToolStore(Protocol):
    """工具持久化存储 —— 对应 harness: app/store/database.go PluginStore。

    工具可以在 DB / 配置文件中定义，运行时按名 + 版本解析。
    """

    async def find(self, name: str, version: str | None = None) -> ToolDefinition: ...
    async def list(self, filter: ToolFilter | None = None) -> list[ToolDefinition]: ...
    async def upsert(self, definition: ToolDefinition) -> ToolDefinition: ...
    async def delete(self, name: str) -> None: ...


class PluginResolver:
    """运行时按名解析工具 —— 对应 harness: app/pipeline/resolver/resolve.go。

    harness 的做法:
      1. pipeline YAML 引用 plugin "my-plugin@v1"
      2. resolver.Resolve(name="my-plugin", version="v1")
      3. pluginStore.Find(name, version) → Plugin.Spec (YAML template)
      4. parse YAML → compile to step config → execute

    迁移到工具层:
      1. LLM 请求调用 tool "web_search"
      2. registry.get("web_search") → BaseTool
      3. tool.execute(**arguments) → ToolResult
    """

    def __init__(self, store: ToolStore):
        self._store = store

    async def resolve(self, name: str, version: str | None = None) -> BaseTool:
        """按名解析工具定义，构造可执行工具实例。

        对应: harness resolver.Resolve() → pluginStore.Find()
        """
        definition = await self._store.find(name, version)
        return self._definition_to_tool(definition)


# ═══════════════════════════════════════════════════════════
# 模式三: ToolFactory —— 对应 harness secret.ResolverFactory 自注册
# ═══════════════════════════════════════════════════════════

class ToolFactory:
    """工具工厂 —— 对应 harness: secret.ResolverFactory + IDE Factory。

    设计来源 (三个 harness factory 的融合):
    - secret/resolver_factory.go: NewFactoryWithProviders(resolvers ...Resolver)
      → 可变参构造，每个 resolver.Type() 自声明键
    - ide/factory.go:           map[enum.IDEType]IDE
      → 枚举键索引
    - container/orchestrator_factory.go: map[enum.InfraProviderType]Orchestrator
      → 按类型获取实现
    """

    def __init__(self):
        self._tools: list[BaseTool] = []

    def add(self, tool: BaseTool) -> "ToolFactory":
        """注册工具 —— 等价于 harness 的工厂可变参构造。"""
        self._tools.append(tool)
        return self

    def build_registry(self) -> ToolRegistry:
        """构造注册表并封禁 —— 等价于 harness executor.finishRegistration()。"""
        registry = ToolRegistry()
        for tool in self._tools:
            registry.register(tool)
        registry.freeze()
        return registry

    @classmethod
    def create_default(cls) -> ToolRegistry:
        """创建默认工具集 —— 对应 harness job/scheduler.go createNecessaryJobs()。"""
        factory = cls()
        factory.add(DynamicTool(
            name="calculator",
            description="执行数学表达式计算，支持 + - * / ** % 等运算符",
            parameters={
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "数学表达式，如 '2 + 3 * 4'",
                    },
                },
                "required": ["expression"],
            },
            execute_func=_calculator_execute,
        ))
        factory.add(DynamicTool(
            name="file_read",
            description="读取指定文件的内容",
            parameters={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "文件路径"},
                },
                "required": ["file_path"],
            },
            execute_func=_file_read_execute,
        ))
        factory.add(DynamicTool(
            name="web_search",
            description="搜索互联网获取实时信息",
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索关键词"},
                },
                "required": ["query"],
            },
            execute_func=_web_search_execute,
        ))
        return factory.build_registry()
```

---

## LLM Prompt 注入机制

这是 harness 中没有的东西——harness 的 pipeline 和 job 都不是 LLM 驱动的。但这个机制直接继承自 harness 的 **模式组合**：

```
harness 模式                          → LLM 工具注入
─────────────────────────────────────────────────────
Triggerer 解析 YAML 生成 Stage[]      → Agent Loop 从 registry 获取 tool definitions
Scheduler 把 Stage 分配给 Runner       → Agent Loop 把 tools[] 注入 LLM 请求
Runner 编译为 Executable              → LLM 返回 tool_call，编排层解析
Manager 生命周期回调                   → 每次 tool_call 前后的事件记录
```

```python
# ═══════════════════════════════════════════════════════════
# 注入点一：Agent Loop 初始化时收集工具定义
# ═══════════════════════════════════════════════════════════

class AgentLoop:
    """harness 中不存在此类 —— 这是把 harness 的 Triggerer + Scheduler
    合并到 LLM Agent 的上下文。
    """

    def __init__(self, registry: ToolRegistry, executor: ToolExecutor, model: LLMModel):
        self._registry = registry
        self._executor = executor
        self._model = model

    async def run(self, user_message: str, max_turns: int = 10) -> str:
        messages = [{"role": "user", "content": user_message}]

        for turn in range(max_turns):
            # ── 注入点: 每次 LLM 调用前收集工具定义 ──
            # 等价于 harness Triggerer 中解析 YAML 生成 Stage 列表
            tools = self._registry.list_for_llm()
            # → [{"type": "function", "function": {...}}, ...]

            response = await self._model.generate(
                messages=messages,
                tools=tools,           # ← 工具定义注入 LLM prompt
                tool_choice="auto",
            )

            if not response.tool_calls:
                return response.content

            # ── 执行点: LLM 返回的 tool_call 在这里落地执行 ──
            # 等价于 harness Scheduler.Schedule() + Runner.Run()
            for tc in response.tool_calls:
                result = await self._executor.execute(tc.name, tc.arguments)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result.output),
                })

        return "max turns exceeded"

# ═══════════════════════════════════════════════════════════
# 注入点二：System Prompt 中的工具描述
# ═══════════════════════════════════════════════════════════

def build_system_prompt(registry: ToolRegistry, user_context: dict) -> str:
    """构建 System Prompt —— 描述工具能力。

    这个机制 harness 中没有直接对应，但遵循相同的"定义 → 注入"模式:
    harness pipeline 将 step 定义注入 runner compiler
    LLM system prompt 将 tool 定义注入 LLM context
    """
    tool_descriptions = "\n".join(
        f"- **{t.name}**: {t.description}" for t in registry.list_all()
    )
    return f"""You are an AI assistant with access to the following tools:

{tool_descriptions}

When you need to use a tool, respond with a function call.
After receiving tool results, continue the conversation.
"""
```

---

## 生命周期管理

完全对应 harness Pipeline Manager 的 `BeforeStage → BeforeStep → AfterStep → AfterStage`：

```python
# harness: app/pipeline/manager/manager.go ExecutionManager interface
# → 每个执行阶段的前后回调

class ToolExecutionManager(Protocol):
    """工具执行的生命周期管理。

    对应 harness pipeline manager 的回调链:
    - BeforeStage  → create steps, update execution status, emit SSE
    - BeforeStep   → create log stream, update step status
    - AfterStep    → persist step result, delete log stream
    - AfterStage   → schedule downstream, finalize execution

    harness: manager/setup.go, manager/teardown.go, manager/updater.go
    """

    async def before_execution(self, task: AgentTask) -> None:
        """执行前: 创建日志流、更新状态、记录开始时间。"""
        ...

    async def before_tool(self, step: ToolStep) -> None:
        """单个工具执行前: 创建日志流、更新步骤状态。"""
        ...

    async def after_tool(self, step: ToolStep) -> None:
        """单个工具执行后: 持久化结果、释放资源。"""
        ...

    async def after_execution(self, task: AgentTask) -> None:
        """执行后: 计算最终状态、调度后续任务（如 DAG downstream）。"""
        ...

    async def write_log(self, step_id: int, line: str) -> None:
        """流式写日志 —— 对应 harness 的 logStream.Write()。"""
        ...


class ToolOrchestrator:
    """协调 registry + executor + lifecycle manager 的工具执行编排器。

    对应 harness:
    - Scheduler 决定何时执行
    - Runner 执行
    - Manager 管理生命周期

    融合为一个编排器。
    """

    def __init__(
        self,
        registry: ToolRegistry,
        executor: ToolExecutor,
        manager: ToolExecutionManager,
    ):
        self._registry = registry
        self._executor = executor
        self._manager = manager

    async def execute(self, task: AgentTask) -> ToolResult:
        """执行一次工具调用，带完整生命周期管理。"""
        step = ToolStep(
            name=task.tool_name,
            input=task.arguments,
            status=ToolStepStatus.PENDING,
        )

        # BeforeStep —— harness: manager.setup.go
        await self._manager.before_tool(step)
        step.status = ToolStepStatus.RUNNING

        # Execute —— harness: runner.run()
        result = await self._executor.execute(task.tool_name, task.arguments)

        # AfterStep —— harness: manager.teardown.go
        step.status = ToolStepStatus.SUCCESS if result.success else ToolStepStatus.FAILURE
        step.output = result.output
        step.error = result.error
        await self._manager.after_tool(step)

        return result
```

---

## Skill 与 MCP 的设计

### Skill：多工具编排

对应 harness Pipeline 的 DAG 模式。Pipeline YAML 中 stages 之间有 `depends_on` 关系，harness 用 DAG 求解执行顺序：

```python
# harness: app/pipeline/triggerer/dag/dag.go
# harness: types/step.go Step.DependsOn []string
#
# → Skill 就是一个小型 pipeline，在工具层内递归执行

class SkillTool(BaseTool):
    """组合多个子工具的高级能力。

    设计来源:
    - harness pipeline DAG (triggerer/dag/dag.go):
      多个 stage 之间有 depends_on 依赖关系
    - harness step.DependsOn (types/step.go:18):
      step 声明依赖哪些其他 step
    - harness manager 的 teardown 逻辑:
      一个 stage 完成后调度下游 stage

    对应关系:
      Pipeline Stage  ──→  Skill Step
      Stage DAG        ──→  Skill 内部步骤依赖图
      depends_on       ──→  step["depends_on"]
      Runner.Run()     ──→  SkillTool.execute()
    """

    def __init__(
        self,
        name: str,
        description: str,
        steps: list[dict],          # 每个 step: {tool, arguments, depends_on}
        registry: ToolRegistry,
    ):
        self._name = name
        self._description = description
        self._steps = steps
        self._registry = registry

    @property
    def name(self) -> str: return self._name

    @property
    def description(self) -> str: return self._description

    @property
    def parameters(self) -> dict:
        """聚合所有步骤的参数 —— 对应 harness pipeline YAML 的 input schema。"""
        return {
            "type": "object",
            "properties": {
                step["tool"]: self._registry.get(step["tool"]).parameters
                for step in self._steps
            },
        }

    @property
    def tool_type(self) -> ToolType: return ToolType.SKILL

    async def execute(self, **kwargs) -> ToolResult:
        """按 DAG 拓扑序执行所有步骤 —— 对应 harness Scheduler + Runner。"""
        results: dict[str, ToolResult] = {}
        completed: set[str] = set()

        for step in self._topological_order():
            # 检查依赖是否全部成功 —— 对应 harness triggerer/dag.go
            for dep in step.get("depends_on", []):
                if dep not in completed:
                    return ToolResult(success=False, error=f"dependency '{dep}' not completed")

            result = await self._registry.execute(step["tool"], step["arguments"])
            results[step["tool"]] = result

            if not result.success:
                return ToolResult(success=False, error=f"step '{step['tool']}' failed: {result.error}")

            completed.add(step["tool"])

        return ToolResult(success=True, output=results)

    def _topological_order(self) -> list[dict]:
        """构建 DAG 拓扑序 —— 对应 harness: triggerer/dag/dag.go。"""
        # ... 用 depends_on 构建 DAG，返回拓扑排序后的步骤列表
        return self._steps
```

### MCP：外部工具协议

对应 harness Plugin Resolution 模式——工具定义不在本地，运行时按名从外部解析：

```python
# harness: resolver.Resolve(name, version) → pluginStore.Find() → Plugin.Spec
#
# → MCP 工具: list_tools() 从 MCP Server 获取定义
#            call_tool() 通过 MCP Client 执行

class MCPToolAdapter(BaseTool):
    """将 MCP Server 的工具适配为 BaseTool。

    设计来源: harness 的 plugin resolution 模式
    - resolver.Resolve(name="my-plugin", version="v1") → pluginStore.Find()
      → parse Plugin.Spec (YAML template) → 替换到 pipeline
    - MCP:
      list_tools() → ToolDefinition[]
      call_tool()  → ToolResult
    """

    def __init__(self, server_name: str, tool_schema: dict, mcp_client: "MCPClient"):
        self._server = server_name
        self._schema = tool_schema
        self._client = mcp_client

    @property
    def name(self) -> str:
        # 带命名空间避免与本地工具冲突
        return f"mcp.{self._server}.{self._schema['name']}"

    @property
    def description(self) -> str:
        return f"[MCP/{self._server}] {self._schema['description']}"

    @property
    def parameters(self) -> dict:
        return self._schema["inputSchema"]

    @property
    def tool_type(self) -> ToolType:
        return ToolType.MCP

    async def execute(self, **kwargs) -> ToolResult:
        try:
            result = await self._client.call_tool(
                server=self._server,
                tool=self._schema["name"],
                arguments=kwargs,
            )
            return ToolResult(success=True, output=result.content)
        except Exception as e:
            return ToolResult(success=False, error=str(e))
```

---

## 完整目录结构

```
tools/
├── __init__.py              # 公共 API 导出
│
├── types.py                 # 枚举 + 数据类（零依赖）
│   # ToolType, ToolStepStatus, ToolResult, ToolDefinition, ExecutionContext
│   # 来源: harness job/types.go + types/step.go
│
├── base.py                  # 核心抽象
│   # BaseTool(ABC), DynamicTool, SkillTool
│   # 来源: harness job/executor.go Handler interface
│
├── registry.py              # 工具注册表
│   # ToolRegistry: register/freeze/execute/list_for_llm
│   # 来源: harness job/executor.go Executor struct
│
├── executor.py              # 可插拔执行器
│   # ToolExecutor(Protocol), InProcessExecutor, DockerExecutor, SubprocessExecutor
│   # 来源: harness app/pipeline/runner/runner.go + job/executor.go
│
├── orchestrator.py          # 执行编排器
│   # ToolOrchestrator, ToolExecutionManager
│   # 来源: harness app/pipeline/manager/manager.go
│
├── factory.py               # 工具工厂
│   # ToolFactory, ToolStore(Protocol), PluginResolver
│   # 来源: harness resolver/resolve.go + secret/resolver_factory.go
│
├── mcp/                     # MCP 支持
│   ├── __init__.py
│   ├── client.py            #   MCP Client 协议栈
│   └── adapter.py           #   MCP Tool → BaseTool 适配器
│       # 来源: harness resolver plugin 解析模式
│
├── builtin/                 # 内置工具
│   ├── __init__.py
│   ├── calculator.py        #   安全 eval
│   ├── file_read.py         #   文件读取
│   └── web_search.py        #   网络搜索
│       # 来源: harness ai_agent.go function map 模式
│
└── runner.py                # 独立进程入口（Docker 隔离用）
    # 对应 harness 容器内执行路径
```

### 依赖方向

```
types.py  ←  base.py  ←  registry.py  ←  orchestrator.py  ←  (Agent Loop)
   ↑           ↑            ↑               ↑
   └───────────┴────────────┴───────────────┘
              executor.py (独立)
           factory.py (独立)
              mcp/ (独立，通过 adapter 挂载到 base.py)
```

---

## 关键设计决策

| 决策 | 说明 | hararness 来源 |
|------|------|:---:|
| **ToolResult 统一返回值** | 异常永不向上泄漏，编排层永远看到 ToolResult | `job/executor.go:103` recover() |
| **freeze() 注册封禁** | 启动后禁止注册新工具，防止运行时注入 | `job/executor.go:90` finishRegistration() |
| **DynamicTool 函数引用** | 简单工具不建新类，用函数引用构造 | `ai_agent.go:42` map[enum]func |
| **工具定义与执行分离** | list_for_llm() 返回定义，execute() 执行，两条独立路径 | pipeline: compiler 编译 vs runner 执行 |
| **可插拔执行器** | InProcess/Docker/subprocess 可切换 | pipeline runner (Docker) vs job executor (进程内) |
| **生命周期回调** | BeforeStep/AfterStep 管理日志流、状态、持久化 | `app/pipeline/manager/` BeforeStage/AfterStage |
| **Plugin 解析模式** | 工具可按名 + 版本从 DB 解析 | `resolver.Resolve()` → `pluginStore.Find()` |
| **构造函数注入** | 所有依赖通过构造函数注入，无 import 副作用 | Google Wire (`wire.go`) |
| **事件驱动触发** | 工具可选通过事件系统异步触发 | `app/events/aitask/` AITaskEventStart |
| **进度回调** | 长时间工具通过 ProgressReporter 上报进度 | `job/executor.go:119` ProgressReporter |
