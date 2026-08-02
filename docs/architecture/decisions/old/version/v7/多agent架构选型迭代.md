# 第一轮选择（deepseek v4 pro）
---
要在 HpAgent 中同时支持 Supervisor、Workflow、Handoff 和 Council 四种架构模式，关键在于抽象出一套 **“不变”的原子能力**，并让编排逻辑成为 **“可插拔的策略”**。以下是一套面向 HpAgent 的抽象接口设计方案。

### 🧱 核心抽象：三大接口 + 一个总线

我们可以把所有模式解构为三种基本角色的交互：

| 角色             | 职责                                                         | 对应 HpAgent 现有模块      |
| :--------------- | :----------------------------------------------------------- | :------------------------- |
| **Agent**        | 执行单个任务的原子单元，内部封装 ReAct 循环、工具调用等。    | `HarnessRunner` 的部分能力 |
| **Orchestrator** | 掌握 **如何** 将任务分配给 Agent、**以什么顺序** 执行、**如何汇总** 结果。 | 新模块，替代旧的单例入口   |
| **Task**         | 描述“要做什么”的标准化数据结构，包括目标、上下文、依赖、状态等。 | `Activity` 或自定义协议    |
| **MessageBus**   | 所有角色间的通信基础设施，支持同步/异步、点对点/广播，是 Handoff 和 Council 的基础。 | 内部消息队列或直接函数调用 |

### 📐 接口定义（Python 伪代码）

#### 1. `Agent` – 原子执行单元

```python
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

class BaseAgent(ABC):
    """所有子Agent的基类，封装了一次完整的任务执行过程。"""
    
    @property
    @abstractmethod
    def capability(self) -> str:
        """返回此Agent的能力描述，用于注册与发现。"""
        ...

    @abstractmethod
    async def execute(self, task: "Task", context: "ExecutionContext") -> "TaskResult":
        """执行一个给定的任务，并返回结果。
        
        内部可以包含完整的 ReAct 循环、工具调用等。
        这是 Agent 对外的唯一执行入口。
        """
        ...
    
    async def stream_execute(self, task: "Task", context: "ExecutionContext") -> AsyncIterator[Any]:
        """可选的流式执行接口，用于实时返回中间步骤。"""
        ...
```

#### 2. `Task` & `TaskResult` – 标准化载荷

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

@dataclass
class Task:
    task_id: str
    goal: str                    # 人类可读的目标描述
    required_capability: str     # 所需的Agent能力标签，用于路由
    input_data: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING

@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[str] = None
```

#### 3. `Orchestrator` – 可插拔的编排策略

```python
class BaseOrchestrator(ABC):
    """编排器基类：将复杂目标拆解为Task，并驱动Agent执行。"""
    
    def __init__(self, agents: list[BaseAgent], bus: "MessageBus"):
        self.agents = {a.capability: a for a in agents}
        self.bus = bus

    @abstractmethod
    async def run(self, goal: str, global_context: dict) -> "OrchestrationResult":
        """执行一个顶层目标，返回最终聚合结果。"""
        ...
```

通过派生不同的 `Orchestrator` 实现，即可支持四种模式：

*   **SupervisorOrchestrator**：内部维护一个“总管 LLM”，动态生成 `Task` 列表，再调用 `Agent.execute()`。
*   **WorkflowOrchestrator**：依据预定义的 DAG 配置，按顺序或条件调用 `Agent.execute()`。
*   **HandoffOrchestrator**：Agent 执行过程中可调用 `bus.handoff(target_capability, new_task)`，将控制权移交出去。Orchestrator 本身只负责启动第一个 Agent 并监听总线。
*   **CouncilOrchestrator**：将同一个 `Task` 并行发送给多个 `Agent`，通过 `bus.collect_responses()` 收集结果，再提交给“议长”模型裁决。

#### 4. `MessageBus` – 通信与交接基础

```python
class MessageBus(ABC):
    """支持点对点、广播和交接的消息传递抽象。"""
    
    @abstractmethod
    async def send(self, target_capability: str, message: "Message") -> None:
        """向特定Agent发送消息。"""
        ...
    
    @abstractmethod
    async def broadcast(self, message: "Message", capabilities: list[str] = None) -> list["Message"]:
        """向多个Agent广播并收集回复，用于 Council 模式。"""
        ...
    
    @abstractmethod
    async def handoff(self, from_agent: str, to_capability: str, task: Task) -> TaskResult:
        """显式的控制权交接，同时记录交接链。"""
        ...
    
    @abstractmethod
    async def listen(self, agent_capability: str) -> AsyncIterator["Message"]:
        """Agent可订阅属于自己的消息流，实现自主触发。"""
        ...
```

### 🔧 与 HpAgent 现有组件融合

将这些抽象映射到 HpAgent 的实际目录结构，可以这样落地：

```
src/
├── harness/                      ← 对现有 HarnessRunner 的改造
│   ├── agent.py                  # BaseAgent, ReActAgent (封装现有推理/工具循环)
│   ├── task.py                   # Task, TaskResult 定义
│   ├── orchestrator/             # 可插拔的编排策略
│   │   ├── base.py               # BaseOrchestrator
│   │   ├── supervisor.py
│   │   ├── workflow.py
│   │   ├── handoff.py
│   │   └── council.py
│   └── bus.py                    # MessageBus 的默认实现（内存/Redis）
├── temporal/
│   └── workflows.py              # 将 Orchestrator.run() 封装为 Temporal Workflow
└── sandbox/                      # 工具执行器，作为 Agent 的基础能力层
```

**如何利用现有基础**：
*   `HarnessRunner` 的核心循环（推理→行动→观察）可直接重构为 `ReActAgent` 类，实现 `BaseAgent.execute()`。
*   `Temporal` 的 Workflow 只需调用 `Orchestrator.run()`，而每个 `Agent.execute()` 可封装为一个 Activity，获得重试和持久性。
*   `nsjail` 和 `Hindsight` 记忆作为 Agent 内部的“技能”和“记忆”，对上层编排完全透明。

### 💎 总结：以不变应万变

这套抽象的核心哲学是：

> **Agent 是动作，Task 是数据，Orchestrator 是策略，MessageBus 是脉络。**

通过固定动作与数据的接口，你可以在不修改任何 Agent 实现的情况下，自由切换或组合四种编排策略。例如，一个复杂系统可能使用 **Supervisor 作为顶层，其子任务中的一个 Workflow 节点内部使用 Handoff 处理多领域路由，而最终决策再交给 Council 投票**——所有这一切，都建立在同一套 `BaseAgent` 和 `Task` 之上。

&nbsp;
&nbsp;

# 第一轮架构评审（mimo v2.5 pro）

先说结论：**顶层设计方向对了——Agent/Orchestrator/Task/Bus 四层分离是正确的战略直觉。但在战术层面，这套方案至少有 7 处硬伤，其中 3 处会导致实际落地时架构变形。**

---

## 一、致命问题（不改会崩）

### 1. Handoff 模式根本不住进 Orchestrator 里

你的四模式都用了 `Orchestrator.run()` 作为入口，但 Handoff 的控制权在 **Agent 侧**——Agent 执行到一半决定移交，这是 agent-driven routing，不是 orchestrator-driven scheduling。

```python
# 你在 HandoffOrchestrator 里写了：
# "Orchestrator 本身只负责启动第一个 Agent 并监听总线"
```

这就暴露了问题：一个"只负责启动和监听"的 Orchestrator 根本不是 Orchestrator，它是一个 **EventLoop** 或 **Runner**。你为了四模式统一入口，强行把不同控制流模型塞进同一个接口，结果就是 HandoffOrchestrator 变成一个空壳，里面塞满与 Orchestrator 职责不匹配的事件驱动逻辑。

**后果**：当你要实现 Supervisor 内嵌 Handoff（你总结里提到的场景）时，子任务的 Handoff 逻辑没法自然地委托给 HandoffOrchestrator，因为控制流模型冲突。

**修正方向**：把"谁发起的控制流转移"抽成独立概念：

```python
class ControlStrategy(ABC):
    """编排的控制策略：谁决定下一个 Task 由谁执行。"""

    @abstractmethod
    async def decide_next(
        self,
        completed: TaskResult,
        remaining: list[Task],
        agent_registry: AgentRegistry,
        bus: MessageBus,
    ) -> Optional[Task]:  # 返回 None 表示编排结束
        ...
```

- Supervisor：由 LLM 决定
- Workflow：由 DAG 定义决定
- Handoff：由当前 Agent 通过 bus 发出信号决定
- Council：由裁决模型决定

Orchestrator 变成一个 **executor loop**，它不关心"如何决策下一步"，只关心"驱动循环直到完成"。

---

### 2. MessageBus 上长了一个不该长的器官：`handoff()`

```python
class MessageBus(ABC):
    async def handoff(self, from_agent, to_capability, task) -> TaskResult: ...
```

`handoff` 是**编排语义**（控制权转移、链路追踪、状态迁移），不是**通信语义**（消息传递、广播、订阅）。把它放在 MessageBus 里，直接导致两个后果：

- MessageBus 从"哑管道"变成了"智能路由器"，职责膨胀
- Agent 无法独立于 MessageBus 进行单元测试，因为 handoff 逻辑耦合在通信层里

**修正**：MessageBus 只管 `send`、`broadcast`、`listen`。handoff 作为 Orchestrator 或 ControlStrategy 层的高级操作，底层通过 `bus.send()` 实现，上层封装交接链追踪和控制权记录。

---

### 3. Orchestrator 的可组合性是个空头支票

你在总结里承诺：

> "Supervisor 作为顶层，其子任务中一个 Workflow 节点内部使用 Handoff"

但 `BaseOrchestrator` **不是 `BaseAgent`**。这意味着 Supervisor 的子任务列表里不能放一个 WorkflowOrchestrator——它不是可调度的执行单元。

**后果**：要么每种 Orchestrator 都要写 adapter 把自己伪装成 Agent，要么可组合性根本做不到。

**修正**：Orchestrator 必须同时实现 `BaseAgent` 接口（或者存在一个 `OrchestratorAsAgent` adapter）。这样任何 Orchestrator 都可以被上层 Orchestrator 当作一个 Agent 来调度，实现真正的递归组合：

```python
class OrchestratorAsAgent(BaseAgent):
    """把一个 Orchestrator 包装为 Agent，使其可被上层编排调度。"""
    def __init__(self, orchestrator: BaseOrchestrator):
        self._orchestrator = orchestrator

    @property
    def capability(self) -> str:
        return f"orchestrator:{self._orchestrator.__class__.__name__}"

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        result = await self._orchestrator.run(task.goal, task.input_data)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=result)
```

---

## 二、严重问题（会导致工程上的大量妥协）

### 4. Task 的 `dependencies` 和 `required_capability` 是两个互斥的路由哲学混在一起

- `required_capability: str` → **能力路由**（我需要能做 X 的 Agent）
- `dependencies: list[str]` → **DAG 编排**（我需要等 task A 和 B 完成）

这两个概念在同一个 dataclass 里，意味着 Task 的定义者必须同时知道"我要什么能力"和"我在 DAG 里的位置"。但实际上：

- Supervisor 模式下，Task 只关心能力，不关心 DAG
- Workflow 模式下，Task 只关心 DAG，能力由配置映射

**修正**：把依赖关系从 Task 移到 Orchestrator 层。Task 只描述"做什么"，DAG 描述"怎么做"：

```python
@dataclass
class Task:
    task_id: str
    goal: str
    required_capability: str
    input_data: dict = field(default_factory=dict)
    # 去掉 dependencies

@dataclass  
class ExecutionPlan:
    """Orchestrator 层的编排描述，与 Task 解耦。"""
    tasks: list[Task]
    edges: dict[str, list[str]]   # task_id -> 依赖的 task_ids
    conditions: dict[str, Callable] = field(default_factory=dict)  # 条件分支
```

---

### 5. 没有状态管理，`global_context: dict` 是一个黑洞

```python
async def run(self, goal: str, global_context: dict) -> OrchestrationResult:
```

`global_context: dict` 承担了至少四个不同职责：共享记忆、中间结果、用户会话状态、运行时配置。全部塞进一个无类型 dict，意味着：

- Agent 之间通过隐式 key 约定传递数据，无契约
- 没有读写隔离，任何 Agent 都能篡改任何状态
- 没有持久化语义，Temporal 场景下重启会丢状态

**修正**：至少拆成三层：

```python
@dataclass
class ExecutionContext:
    session: SessionState          # 用户会话、对话历史
    shared_memory: SharedMemory    # Agent 间可读写的键值存储（有 TTL 和隔离策略）
    config: RuntimeConfig          # 超时、重试、模型选择等
```

---

### 6. 错误处理和补偿是真空地带

`TaskResult.error: Optional[str]` 就完了？考虑这些场景：

- Agent 执行超时 → Orchestrator 怎么知道？TaskResult 根本不会被返回
- Handoff 链中间一环失败 → 已完成的 Agent 结果怎么回滚或重试？
- Council 中 3/5 的 Agent 失败 → 少数派结果是否采纳？阈值是多少？
- Temporal Activity 失败 → Saga 补偿逻辑在哪里？

**修正**：TaskResult 需要扩展，Orchestrator 层需要错误策略接口：

```python
@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None  # 结构化错误，包含类型、重试建议、部分产出
    metrics: ExecutionMetrics = None    # 耗时、token 用量等

class ErrorStrategy(ABC):
    @abstractmethod
    async def on_failure(self, task: Task, error: ErrorInfo, context: ExecutionContext) -> RetryDecision:
        """决定重试、跳过、降级还是终止。"""
        ...
```

---

### 7. 没有 Agent 生命周期和可观测性

你的 Agent 接口只有 `execute`，但真实系统中至少需要：

- **健康检查**：Agent 启动前/运行中/销毁后的状态管理
- **资源声明**：Agent 声明自己需要的资源（GPU、内存、特定 API key）
- **可观测性钩子**：每次 execute 前后的 trace span、metrics 上报
- **并发控制**：一个 Agent 能同时处理几个 Task？

```python
class BaseAgent(ABC):
    @abstractmethod
    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        ...

    # 缺失的：
    async def initialize(self) -> None: ...          # 启动钩子
    async def shutdown(self) -> None: ...             # 关闭钩子
    async def health_check(self) -> AgentHealth: ...  # 健康状态
    @property
    def max_concurrency(self) -> int: ...             # 并发上限
```

---

## 三、值得注意的设计债务

### 8. `capability: str` 的路由粒度不足

字符串匹配在原型阶段够用，但生产环境中会出现："我需要一个能写 Python 的 Agent，优先选代码质量高的，如果都忙就选便宜的"。建议预留 `CapabilitySpec` 结构，至少支持标签匹配和优先级：

```python
@dataclass
class CapabilitySpec:
    tags: set[str]           # {"code-generation", "python"}
    priority: int = 0        # 同标签多 Agent 时的优先级
    cost_tier: str = "default"
```

### 9. 没有共享记忆层

Council 模式需要多个 Agent 基于**相同的信息**进行独立推理再聚合。但当前设计中，信息只能通过 Task.input_data 传入。如果 Council 的前置环节产生了中间数据，怎么共享？是全部塞进 Task.input_data，还是有一个独立的 SharedMemory？

### 10. Temporal 集成不只是"包一层"

```python
# 你说的：
# 将 Orchestrator.run() 封装为 Temporal Workflow
# 每个 Agent.execute() 封装为一个 Activity
```

但 Temporal 的确定性重放要求 Workflow 内不能有非确定性操作（直接 LLM 调用、随机数等）。Orchestrator 内部的 LLM 调用（Supervisor 的 plan 生成）必须封装为 Activity，而不是留在 Workflow 代码里。这一约束会反过来影响 Orchestrator 的实现方式。

---

## 总结：一张优先级排序表

| 优先级 | 问题                                  | 影响               | 修复成本 |
| :----- | :------------------------------------ | :----------------- | :------- |
| **P0** | Handoff 控制流模型不属于 Orchestrator | 架构根基不稳       | 中       |
| **P0** | Orchestrator 不可组合                 | 四模式无法嵌套     | 低       |
| **P0** | MessageBus 上长了 handoff             | 职责混乱，测试困难 | 低       |
| **P1** | Task 职责混杂（能力+DAG）             | 接口不正交         | 中       |
| **P1** | 无状态管理                            | 生产环境必崩       | 中       |
| **P1** | 无错误补偿                            | 链路故障无法恢复   | 中       |
| **P1** | Agent 无生命周期                      | 运维不可能         | 低       |
| **P2** | 路由粒度不足                          | 功能天花板         | 低       |
| **P2** | 无共享记忆                            | Council 模式受限   | 中       |
| **P2** | Temporal 确定性约束                   | 实现时会返工       | 中       |

**方向是对的，但接口层还需要一轮重构才能真正落地。** 核心改动是：引入 ControlStrategy 解耦控制流决策，让 Orchestrator 退化为纯执行循环，同时确保 Orchestrator 可以被当作 Agent 递归组合。

&nbsp;
&nbsp;


# 第一轮修改(deepseek v4 pro)

基于上一轮尖锐的架构评审，我对抽象接口进行了彻底的重构。核心改动是：**引入 `ControlStrategy` 解耦控制流决策，让 `Orchestrator` 退化为纯执行循环，并确保一切皆 `Agent` 以实现递归组合**。

以下是修正后的完整方案。

---

## 改造后的 Harness 抽象接口

### 1. 原子执行单元：`BaseAgent`

```python
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Optional

class BaseAgent(ABC):
    """原子执行单元，封装完整的 ReAct 循环或工具调用。"""

    @property
    @abstractmethod
    def capability(self) -> "CapabilitySpec":
        """返回此 Agent 的能力声明（标签、优先级、成本等）。"""
        ...

    @abstractmethod
    async def execute(self, task: "Task", context: "ExecutionContext") -> "TaskResult":
        """执行给定任务，返回结果。内部可包含完整的推理-行动循环。"""
        ...

    # --- 生命周期 ---
    async def initialize(self) -> None:
        """启动钩子，进行资源分配、健康检查注册等。幂等实现。"""

    async def shutdown(self) -> None:
        """关闭钩子，释放资源。"""

    async def health_check(self) -> "AgentHealth":
        """返回当前健康状态（HEALTHY, DEGRADED, UNHEALTHY）。"""

    @property
    def max_concurrency(self) -> int:
        """该 Agent 能同时处理的 Task 数量上限。"""
        return 1
```

### 2. 标准化任务与结果

```python
from dataclasses import dataclass, field
from enum import Enum

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"   # 新增：正在回滚
    COMPENSATED = "compensated"     # 新增：已回滚

@dataclass
class Task:
    """任务：纯粹的“做什么”声明，不包含编排依赖。"""
    task_id: str
    goal: str                        # 人类可读的目标
    required_capability: "CapabilityRequirement"  # 所需能力需求
    input_data: dict = field(default_factory=dict)
    compensation: Optional["CompensationHandler"] = None  # 失败回滚逻辑

@dataclass
class ErrorInfo:
    """结构化错误，替代简单的字符串。"""
    type: str                        # 如 TimeoutError, ToolExecutionError
    message: str
    retryable: bool
    partial_output: Any = None       # 失败前产出的部分结果

@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None
    metrics: Optional["ExecutionMetrics"] = None  # 耗时、token 用量等
```

### 3. 编排计划：`ExecutionPlan`

```
@dataclass
class ExecutionPlan:
    """编排层专用的任务图描述，与 Task 完全解耦。"""
    tasks: dict[str, Task]           # task_id -> Task
    dependencies: dict[str, list[str]]  # task_id -> 依赖的 task_id 列表
    conditions: dict[str, Any] = field(default_factory=dict)  # 条件分支规则
```

### 4. 分层上下文：`ExecutionContext`

```python
@dataclass
class SessionState:
    user_id: str
    conversation_history: list = field(default_factory=list)

@dataclass
class SharedMemory:
    """Agent 间共享的键值存储，支持命名空间隔离和 TTL。"""
    # 实际实现中会包含读写锁、快照等功能
    namespace: dict = field(default_factory=dict)

@dataclass
class RuntimeConfig:
    timeout_seconds: int = 300
    max_retries: int = 3
    model_name: str = "default"

@dataclass
class ExecutionContext:
    session: SessionState
    shared_memory: SharedMemory
    config: RuntimeConfig
    trace_id: str                   # 全链路追踪标识
```

### 5. 纯通信层：`MessageBus`

```python
class MessageBus(ABC):
    """纯粹的哑管道，不包含任何编排语义（如 handoff）。"""

    @abstractmethod
    async def send(self, target_capability: str, message: "Message") -> None:
        """向具有特定能力的 Agent 发送消息。"""
        ...

    @abstractmethod
    async def broadcast(self, message: "Message", capabilities: list[str] = None) -> list["Message"]:
        """广播消息并收集回复，用于 Council 模式。"""
        ...

    @abstractmethod
    async def listen(self, agent_capability: str) -> AsyncIterator["Message"]:
        """Agent 订阅发给它的消息流，支持自主触发。"""
        ...
```

### 6. 控制策略：`ControlStrategy`

```python
class ControlStrategy(ABC):
    """
    编排的控制策略：决定下一个 Task 由谁执行。
    这是 Supervisor/Workflow/Handoff/Council 实现差异的核心。
    """

    @abstractmethod
    async def decide_next(
        self,
        completed: TaskResult,
        plan: ExecutionPlan,
        agent_registry: "AgentRegistry",
        bus: MessageBus,
        context: ExecutionContext
    ) -> Optional[Task]:
        """
        根据当前执行状态，返回下一个待分派的 Task。
        返回 None 表示编排结束。
        """
        ...
```

不同的实现：

- **SupervisorControlStrategy**：内部调用 LLM 生成/更新 ExecutionPlan，再按 Plan 选择下一个就绪任务。
- **WorkflowControlStrategy**：根据预定义的依赖图静态选择下一个就绪任务。
- **HandoffControlStrategy**：监听 MessageBus，当收到 Agent 发来的 `HandoffRequest` 消息时，将其转换为下一个 Task。
- **CouncilControlStrategy**：并行任务执行完毕后，将多个结果交给议长 LLM 裁决，然后终止。

### 7. 执行循环：`Orchestrator`

```python
class Orchestrator:
    """纯执行循环：不关心如何决策，只负责驱动 Agent 完成 ExecutionPlan。"""

    def __init__(self, strategy: ControlStrategy, agents: dict[str, BaseAgent], bus: MessageBus):
        self.strategy = strategy
        self.agents = agents
        self.bus = bus

    async def run(self, plan: ExecutionPlan, context: ExecutionContext) -> dict[str, TaskResult]:
        """执行一个完整的编排计划，返回所有任务的结果。"""
        results = {}
        while True:
            # 用最近完成的任务结果询问策略
            last_result = list(results.values())[-1] if results else None
            next_task = await self.strategy.decide_next(
                last_result, plan, self.agent_registry(), self.bus, context
            )
            if next_task is None:
                break

            agent = self._find_agent(next_task.required_capability)
            result = await agent.execute(next_task, context)
            results[next_task.task_id] = result

            # 错误处理与补偿
            if result.status == TaskStatus.FAILED:
                recovery = await self.error_strategy.on_failure(next_task, result.error, context)
                if recovery.action == "compensate":
                    await self._compensate(plan, results)
                    break
                elif recovery.action == "retry":
                    continue
                else:
                    break
        return results
```

### 8. 可递归组合：`OrchestratorAsAgent`

```python
class OrchestratorAsAgent(BaseAgent):
    """将任何 Orchestrator 包装为 Agent，使其可被上层编排调度。"""

    def __init__(self, orchestrator: Orchestrator, capability_spec: CapabilitySpec):
        self._orchestrator = orchestrator
        self._capability = capability_spec

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        # 从任务目标与输入数据生成子 ExecutionPlan
        sub_plan = self._build_plan_from_task(task)
        sub_results = await self._orchestrator.run(sub_plan, context)
        # 聚合子任务结果为当前 Task 的输出
        aggregated = self._aggregate(sub_results)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=aggregated)
```

### 9. 能力声明与需求匹配

```python
@dataclass
class CapabilitySpec:
    """Agent 侧的能力声明。"""
    tags: set[str]                     # 如 {"code-generation", "python"}
    priority: int = 0
    cost_tier: str = "default"

@dataclass
class CapabilityRequirement:
    """Task 侧的能力需求。"""
    required_tags: set[str]
    min_priority: int = 0
    max_cost: str = "premium"
    allow_degraded: bool = False
```

### 10. 错误处理与补偿

```python
class ErrorStrategy(ABC):
    @abstractmethod
    async def on_failure(self, task: Task, error: ErrorInfo, context: ExecutionContext) -> "RetryDecision":
        """返回重试、跳过、降级、补偿或终止的决策。"""
        ...

class CompensationHandler(ABC):
    @abstractmethod
    async def compensate(self, task: Task, context: ExecutionContext) -> None:
        """回滚已执行任务产生的副作用。"""
        ...
```

---

## 修正后的架构全景

```
┌──────────────────────────────────────────────────────────┐
│                     Orchestrator                          │
│  (Execution Loop: 循环调 decide_next → execute → 处理结果) │
│                                                          │
│  依赖: ControlStrategy, AgentRegistry, MessageBus, ErrorStrategy
└───────┬──────────────────────────────────────────────────┘
        │
        ▼
  ControlStrategy (抽象)
     ├── SupervisorStrategy
     ├── WorkflowStrategy
     ├── HandoffStrategy
     └── CouncilStrategy
        │
        ▼
  BaseAgent (抽象)
     ├── 普通 Agent (ReAct / ToolAgent)
     └── OrchestratorAsAgent (递归组合)
        │
        ▼
  MessageBus (纯通信)  ↔  ExecutionContext (分层状态)
```

## 总结

修订后的抽象具有以下特性：

- **正交分离**：任务定义、控制决策、执行循环、通信全部解耦。
- **可递归组合**：任何编排器都可以被上层编排器当作普通 Agent 使用。
- **生产就绪**：内置生命周期管理、结构化错误处理、补偿机制和分层状态管理。
- **模式全覆盖**：四种架构模式仅通过替换 `ControlStrategy` 实现，无需改动其他组件。

这套接口能直接作为 HpAgent 重构的核心契约，支撑从简单串行到复杂嵌套协作的全部场景。

&nbsp;
&nbsp;


# 第二轮评审（mimo v2.5 pro）

先肯定：上一轮 P0 级的三个问题（Handoff 归属、可组合性、Bus 职责膨胀）都得到了实质性修正，ControlStrategy 的引入是正确的架构决策。整体框架从"能描述"进化到了"能运行"的边缘。

但这一轮引入了新的结构性问题——**执行循环模型本身无法承载四种模式的并发和控制流差异**。这不再是接口设计的微调，而是 Orchestrator 核心循环需要重新思考。

---

## 一、执行循环是断裂点

### 1. 循环是串行的，Council 根本跑不起来

```python
while True:
    next_task = await self.strategy.decide_next(...)  # 返回一个 Task
    agent = self._find_agent(...)
    result = await agent.execute(next_task, ...)       # 同步等待一个结果
    results[next_task.task_id] = result
```

Council 的核心语义是 **同一任务并行发给 N 个 Agent，收集全部结果后裁决**。但 `decide_next` 返回 `Optional[Task]`（单数），循环体是串行的 `execute → 等待 → 下一个`。

CouncilControlStrategy 如果想实现并行，它要么：
- 在 `decide_next` 里把一个 Task 复制 N 份并分发（但循环体只执行一个）
- 把 N 个并行调用伪装成一个 Task 的内部实现（把并行性藏起来，Orchestrator 层完全失去对并行的控制）

两种方案都意味着 Orchestrator 的循环对 Council 模式 **透明但无用**——实际的编排逻辑全部内化到了 Strategy 内部，Strategy 退化成了一个黑盒函数，Orchestrator 的存在失去意义。

**同样受影响的**：Supervisor 场景中，如果多个子任务之间无依赖，最优策略是并行执行。但当前循环无法表达这一点。

**修正**：Orchestrator 的核心循环需要支持 **任务组（task group）** 的并发调度，而非逐任务串行：

```python
class Orchestrator:
    async def run(self, plan: ExecutionPlan, context: ExecutionContext) -> dict[str, TaskResult]:
        results: dict[str, TaskResult] = {}
        pending: set[str] = set(plan.tasks.keys())

        while pending:
            # Strategy 返回一批可并行执行的就绪任务
            ready_batch: list[Task] = await self.strategy.get_ready_batch(
                results, plan, pending, self.bus, context
            )
            if not ready_batch:
                break

            # 并行执行这批任务
            batch_results = await asyncio.gather(
                *[self._execute_single(task, context) for task in ready_batch],
                return_exceptions=True
            )

            for task, result in zip(ready_batch, batch_results):
                results[task.task_id] = result
                pending.discard(task.task_id)

            # Strategy 可以基于本轮批次结果修改 plan（动态添加任务、提前终止等）
            await self.strategy.on_batch_completed(results, plan, context)

        return results
```

这样：
- **Council**：`get_ready_batch` 一次性返回 N 个相同 goal 的 Task → 并行执行 → `on_batch_completed` 触发裁决
- **Supervisor**：每轮 `get_ready_batch` 调 LLM 生成新 Task → 执行 → `on_batch_completed` 决定是否继续
- **Workflow**：`get_ready_batch` 返回当前 DAG 层所有无依赖节点 → 并行执行
- **Handoff**：`get_ready_batch` 返回 Agent 通过 bus 发来的下一个 Task

---

### 2. ControlStrategy.decide_next() 的签名对 Handoff 是断裂的

Handoff 的本质是：**Agent A 在执行过程中决定将控制权移交给 Agent B**。这是一个 **执行期间** 的事件，不是 **两次执行之间** 的事件。

但当前设计中，控制流转移只能发生在 `decide_next` 被调用的时刻，即一个 Agent 的 `execute()` 已经返回之后。这意味着：

- Agent A 在 `execute()` 内部决定 handoff → 但它只能通过返回值或 MessageBus 传递这个意图
- 如果通过返回值：需要一个特殊的 TaskStatus（如 `HANDED_OFF`），Orchestrator 循环体需要识别它
- 如果通过 MessageBus：Agent A 发了一条消息，然后 `execute()` 返回某种"已 handoff"状态，然后 `decide_next` 从 bus 上读取消息

两种方案都要求 **Agent 的返回值中包含编排语义**（"我不只是完成了，我还把控制权交出去了"），这打破了 TaskResult 的纯粹性。

**修正方向**：在 Orchestrator 循环中显式支持控制权转移：

```python
@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None
    handoff_request: Optional[HandoffRequest] = None  # Agent 可在结果中声明移交意图

@dataclass
class HandoffRequest:
    target_capability: CapabilityRequirement
    context_to_pass: dict       # 需要传递给下一个 Agent 的上下文
    reason: str                 # 人类可读的移交原因
```

Orchestrator 循环中：

```python
result = await agent.execute(task, context)

if result.handoff_request:
    # 创建新 Task，注入到 plan 中，让 Strategy 决定如何处理
    handoff_task = Task(
        task_id=generate_id(),
        goal=result.handoff_request.reason,
        required_capability=result.handoff_request.target_capability,
        input_data=result.handoff_request.context_to_pass,
    )
    plan.tasks[handoff_task.task_id] = handoff_task
    # 不立即执行，交给下一轮 Strategy.decide_next 处理
```

这样 Handoff 仍然是声明式的（Agent 说"我想移交"），实际的路由和执行决策仍然由 ControlStrategy 掌控。

---

## 二、Plan 的生命周期是矛盾的

### 3. `run(plan)` 与 Supervisor 动态生成计划的矛盾

```python
async def run(self, plan: ExecutionPlan, context: ExecutionContext) -> dict[str, TaskResult]:
```

方法签名要求调用方传入一个 **已经构建好的** ExecutionPlan。但 Supervisor 模式的本质是 **计划是动态生成的**——LLM 看到 goal 后才知道要拆成哪些 Task。

这就导致了一个问题：谁来调 LLM 生成计划？

- 如果是调用方在调 `run()` 之前：那调用方需要有一个 Planner，但这个 Planner 在 Workflow 模式下不需要，接口不统一
- 如果是 SupervisorStrategy 内部：那 `plan` 参数对 Supervisor 来说是空的/无意义的，Strategy 内部会完全忽略传入的 plan
- 如果是 Orchestrator 循环中通过 Strategy 动态修改 plan：可以，但没有明确的"计划初始化"阶段

**修正**：把"计划生成"从 `run()` 的参数中移除，改为 Strategy 的职责：

```python
class ControlStrategy(ABC):
    @abstractmethod
    async def initialize_plan(self, goal: str, context: ExecutionContext) -> ExecutionPlan:
        """根据顶层目标生成初始执行计划。Supervisor 调 LLM，Workflow 加载 DAG 定义。"""
        ...

    @abstractmethod
    async def get_ready_batch(
        self, results: dict[str, TaskResult], plan: ExecutionPlan,
        pending: set[str], bus: MessageBus, context: ExecutionContext
    ) -> list[Task]:
        ...
```

Orchestrator 改为：

```python
async def run(self, goal: str, context: ExecutionContext) -> dict[str,TaskResult]:
    plan = await self.strategy.initialize_plan(goal, context)
    # ... 循环
```

这样每种 Strategy 自己决定如何初始化计划，Orchestrator 不再需要外部传入 plan。

---

## 三、新引入的接口缺失

### 4. MessageBus 上缺少标准化的协议消息类型

MessageBus 被修正为"纯哑管道"，但 `Message` 类型从未定义。在 Handoff 和 Council 模式中，bus 上传递的消息类型至关重要：

```python
# Handoff 请求（Agent → Bus → Orchestrator/Strategy）
@dataclass
class HandoffMessage:
    from_agent: str
    target_capability: CapabilityRequirement
    payload: dict

# Council 投票结果（Agent → Bus → CouncilStrategy）
@dataclass
class VoteMessage:
    agent_id: str
    task_id: str
    verdict: Any
    confidence: float

# 通用消息
@dataclass
class Message:
    msg_type: str           # "handoff", "vote", "status_update", ...
    sender: str
    payload: dict
    timestamp: float
```

如果 Message 完全是无类型的 `dict`，HandoffControlStrategy 需要在 `listen()` 的流中自行解析消息——这把协议解析逻辑分散到了每个 Strategy 中。

**修正**：定义 `Message` 为 sealed class 或带 discriminated union 的结构，并提供标准的消息类型。

---

### 5. AgentRegistry 从未被正式定义

`decide_next` 的签名引用了 `"AgentRegistry"`，但代码中只有 `self.agents: dict[str, BaseAgent]`。Strategy 需要的是：

```python
class AgentRegistry(ABC):
    @abstractmethod
    async def find(self, requirement: CapabilityRequirement) -> list[BaseAgent]:
        """按能力需求查找匹配的 Agent 列表。"""
        ...

    @abstractmethod
    async def get_healthy_agents(self) -> list[BaseAgent]:
        """返回当前健康的 Agent 列表。"""
        ...

    @abstractmethod
    async def get_availability(self, agent: BaseAgent) -> int:
        """返回 Agent 当前可用的并发槽位。"""
        ...
```

这不是一个"后续补充"的组件——没有它，Strategy 中的路由逻辑无处安放。

---

### 6. OrchestratorAsAdapter 的 `_build_plan_from_task` 是一个伪装成实现的抽象方法

```python
class OrchestratorAsAgent(BaseAgent):
    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        sub_plan = self._build_plan_from_task(task)   # ← 这里
        sub_results = await self._orchestrator.run(sub_plan, context)
        aggregated = self._aggregate(sub_results)      # ← 还有这里
        ...
```

`_build_plan_from_task` 和 `_aggregate` 是整个递归组合机制的核心难题（如何将一个 Task 分解为子计划，如何将子结果聚合为单个输出），但它们被定义为没有接口约束的私有方法。上一轮我说的"可组合性是空头支票"，这里变成了一张**只写了金额没签名的支票**。

**修正**：结合第 3 点的修正，`OrchestratorAsAgent` 的分解逻辑应该由 `ControlStrategy.initialize_plan()` 承担，聚合逻辑需要显式接口：

```python
class ResultAggregator(ABC):
    @abstractmethod
    async def aggregate(self, results: dict[str, TaskResult], context: ExecutionContext) -> Any:
        """将多个子任务结果聚合为一个输出。"""
        ...

class OrchestratorAsAgent(BaseAgent):
    def __init__(self, orchestrator: Orchestrator, capability_spec: CapabilitySpec,
                 aggregator: ResultAggregator):
        self._orchestrator = orchestrator
        self._capability = capability_spec
        self._aggregator = aggregator

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        sub_results = await self._orchestrator.run(task.goal, context)
        output = await self._aggregator.aggregate(sub_results, context)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

---

## 四、设计层面的遗留债务

### 7. ExecutionPlan.conditions 从未被使用

`ExecutionPlan` 定义了 `conditions: dict[str, Any]` 用于条件分支，但 Orchestrator 循环中完全没有消费它的逻辑。Workflow 模式的 if/else 分支、Supervisor 模式的"如果结果不满意则重新规划"，都需要条件判断机制。

### 8. ExecutionMetrics 未定义

`TaskResult.metrics: Optional[ExecutionMetrics]` 被引用但从未定义。对于有成本意识的 Supervisor（需要根据 token 用量决定是否继续）和需要超时管理的 Orchestrator，这不是可选的。

### 9. CompensationHandler 不能序列化

```python
@dataclass
class Task:
    compensation: Optional[CompensationHandler] = None
```

`CompensationHandler` 是一个 ABC，其实例可能包含闭包、数据库连接等不可序列化状态。在 Temporal 场景下，Workflow 可能被序列化到磁盘再恢复——一个包含不可序列化补偿处理器的 Task 会导致恢复失败。

**修正**：补偿逻辑应该通过注册表查找，而非嵌入 Task：

```python
class CompensationRegistry:
    async def register(self, task_type: str, handler: CompensationHandler): ...
    async def compensate(self, task: Task, context: ExecutionContext): ...
```

Task 中只保留 `task_type` 标识符，补偿时通过 registry 查找。

---

## 总结：修正优先级

| 优先级 | 问题 | 本质 |
|:---|:---|:---|
| **P0** | 循环是串行的，Council/并行不成立 | Orchestrator 执行模型缺陷 |
| **P0** | Plan 生命周期矛盾（外部传入 vs 动态生成） | Strategy 职责边界不清 |
| **P0** | Handoff 的控制权转移无表达手段 | Agent→Orchestrator 的信号缺失 |
| **P1** | Message 类型未定义，bus 上无协议 | 通信层缺乏契约 |
| **P1** | AgentRegistry 未正式化 | 路由逻辑无处安放 |
| **P1** | OrchestratorAsAgent 的聚合/分解无接口 | 递归组合仍是概念模型 |
| **P2** | Conditions 未消费 | 条件分支不可用 |
| **P2** | CompensationHandler 不可序列化 | Temporal 兼容性隐患 |

**核心判断**：接口层已经从"方向对"进化到"结构对"，现在卡在 **执行循环模型** 上。把串行 `while True` 改造为支持并发任务组的循环，并引入 `initialize_plan` 让 Strategy 承担计划生命周期，这一轮修正完成后，架构就基本可以进入实现阶段了。





# 第三轮架构（deepseek v4 pro）

基于第二轮评审的尖锐指正，我对抽象接口进行了再次重构。核心改动是：

1. **执行循环从串行升级为并发任务组**，使 Council、Supervisor 的并行子任务能够自然承载。
2. **计划生命周期由 `ControlStrategy.initialize_plan()` 接管**，消除“外部传计划”与 Supervisor 动态生成的矛盾。
3. **引入 `HandoffRequest` 作为 Agent 结果的一部分**，将控制权移交声明化，使 Handoff 模式不破坏执行循环。
4. **补齐缺失的协议与组件**：标准消息类型、`AgentRegistry`、`ResultAggregator`、可序列化补偿注册表。

以下是完整修正后的方案。

---

## 修正后的 Harness 抽象接口

### 1. 原子执行单元：`BaseAgent`

```python
from abc import ABC, abstractmethod
from typing import Optional

class BaseAgent(ABC):
    """原子执行单元，封装完整的 ReAct 循环或工具调用。"""

    @property
    @abstractmethod
    def capability(self) -> "CapabilitySpec":
        """返回此 Agent 的能力声明（标签、优先级、成本等）。"""
        ...

    @abstractmethod
    async def execute(self, task: "Task", context: "ExecutionContext") -> "TaskResult":
        """执行给定任务，返回结果。内部可包含完整的推理-行动循环。"""
        ...

    # --- 生命周期 ---
    async def initialize(self) -> None:
        """启动钩子，进行资源分配、健康检查注册等。幂等实现。"""

    async def shutdown(self) -> None:
        """关闭钩子，释放资源。"""

    async def health_check(self) -> "AgentHealth":
        """返回当前健康状态（HEALTHY, DEGRADED, UNHEALTHY）。"""

    @property
    def max_concurrency(self) -> int:
        """该 Agent 能同时处理的 Task 数量上限。"""
        return 1
```

### 2. 标准化任务与结果（增强）

```python
from dataclasses import dataclass, field
from enum import Enum

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"

@dataclass
class Task:
    """任务：纯粹的“做什么”声明，不包含编排依赖。"""
    task_id: str
    goal: str
    required_capability: "CapabilityRequirement"
    input_data: dict = field(default_factory=dict)
    task_type: str = "default"          # 用于补偿注册表查找
    parent_task_id: Optional[str] = None  # 用于追踪子任务关系

@dataclass
class ErrorInfo:
    type: str
    message: str
    retryable: bool
    partial_output: Any = None

@dataclass
class ExecutionMetrics:
    duration_ms: float = 0.0
    token_usage: dict = field(default_factory=dict)  # {"input": int, "output": int}

@dataclass
class HandoffRequest:
    """Agent 声明移交意图。"""
    target_capability: "CapabilityRequirement"
    context_to_pass: dict
    reason: str

@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None
    metrics: Optional[ExecutionMetrics] = None
    handoff_request: Optional[HandoffRequest] = None   # 编排信号
```

### 3. 能力声明与需求匹配

```python
@dataclass
class CapabilitySpec:
    """Agent 侧的能力声明。"""
    tags: set[str]
    priority: int = 0
    cost_tier: str = "default"

@dataclass
class CapabilityRequirement:
    """Task 侧的能力需求。"""
    required_tags: set[str]
    min_priority: int = 0
    max_cost: str = "premium"
    allow_degraded: bool = False
```

### 4. 分层上下文

```python
@dataclass
class SessionState:
    user_id: str
    conversation_history: list = field(default_factory=list)

@dataclass
class SharedMemory:
    namespace: dict = field(default_factory=dict)

@dataclass
class RuntimeConfig:
    timeout_seconds: int = 300
    max_retries: int = 3
    model_name: str = "default"

@dataclass
class ExecutionContext:
    session: SessionState
    shared_memory: SharedMemory
    config: RuntimeConfig
    trace_id: str
```

### 5. 纯通信层：`MessageBus` + 标准消息

```python
from dataclasses import dataclass
from typing import Union

@dataclass
class HandoffMessage:
    msg_type: str = "handoff"
    sender: str = ""
    target_capability: CapabilityRequirement = None
    payload: dict = field(default_factory=dict)

@dataclass
class VoteMessage:
    msg_type: str = "vote"
    sender: str = ""
    task_id: str = ""
    verdict: Any = None
    confidence: float = 0.0

@dataclass
class StatusMessage:
    msg_type: str = "status"
    sender: str = ""
    task_id: str = ""
    status: str = ""

# 联合消息类型
Message = Union[HandoffMessage, VoteMessage, StatusMessage, dict]

class MessageBus(ABC):
    """纯哑管道，只负责收发。"""

    @abstractmethod
    async def send(self, target_capability: str, message: Message) -> None:
        ...

    @abstractmethod
    async def broadcast(self, message: Message, capabilities: list[str] = None) -> list[Message]:
        ...

    @abstractmethod
    async def listen(self, agent_capability: str) -> "AsyncIterator[Message]":
        ...
```

### 6. 控制策略：`ControlStrategy`（重构）

```python
class ControlStrategy(ABC):
    """
    编排的控制策略：负责计划生成、批量任务调度、动态调整。
    这是 Supervisor/Workflow/Handoff/Council 实现差异的核心。
    """

    @abstractmethod
    async def initialize_plan(self, goal: str, context: ExecutionContext) -> "ExecutionPlan":
        """根据顶层目标生成初始执行计划。"""
        ...

    @abstractmethod
    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: "ExecutionPlan",
        pending: set[str],
        bus: MessageBus,
        context: ExecutionContext
    ) -> list[Task]:
        """
        根据当前执行状态和 plan，返回一批可并行执行的就绪任务。
        返回空列表表示当前无就绪任务（可能需等待或终止）。
        """
        ...

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: "ExecutionPlan",
        context: ExecutionContext
    ) -> None:
        """
        批次完成后回调，允许策略动态修改 plan（如添加补偿任务、提前终止、重新规划）。
        默认空操作。
        """
        pass
```

### 7. 编排计划：`ExecutionPlan`（去掉了外部传参）

```python
@dataclass
class ExecutionPlan:
    tasks: dict[str, Task]           # task_id -> Task
    dependencies: dict[str, list[str]]  # task_id -> 依赖的 task_id 列表
    conditions: dict[str, Any] = field(default_factory=dict)  # 由 Orchestrator 在循环中解析
```

### 8. 执行循环：`Orchestrator`（支持并发任务组）

```python
import asyncio

class Orchestrator:
    def __init__(
        self,
        strategy: ControlStrategy,
        registry: "AgentRegistry",
        bus: MessageBus,
        error_strategy: "ErrorStrategy"
    ):
        self.strategy = strategy
        self.registry = registry
        self.bus = bus
        self.error_strategy = error_strategy

    async def run(self, goal: str, context: ExecutionContext) -> dict[str, TaskResult]:
        plan = await self.strategy.initialize_plan(goal, context)
        results: dict[str, TaskResult] = {}
        pending: set[str] = set(plan.tasks.keys())

        while pending:
            ready_batch = await self.strategy.get_ready_batch(
                results, plan, pending, self.bus, context
            )
            if not ready_batch:
                # 所有任务已完成或无法继续
                break

            # 并行执行
            batch_results = await asyncio.gather(
                *[self._execute_with_lifecycle(task, context) for task in ready_batch],
                return_exceptions=True
            )

            for task, result in zip(ready_batch, batch_results):
                if isinstance(result, Exception):
                    result = TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        error=ErrorInfo(type=type(result).__name__, message=str(result), retryable=False)
                    )
                results[task.task_id] = result
                pending.discard(task.task_id)

                # 处理 handoff 信号
                if result.handoff_request:
                    handoff_task = Task(
                        task_id=f"{task.task_id}_handoff",
                        goal=result.handoff_request.reason,
                        required_capability=result.handoff_request.target_capability,
                        input_data=result.handoff_request.context_to_pass,
                        parent_task_id=task.task_id
                    )
                    plan.tasks[handoff_task.task_id] = handoff_task
                    pending.add(handoff_task.task_id)

            # 批次完成后调用策略回调，允许动态修改 plan
            await self.strategy.on_batch_completed(results, plan, context)

            # 错误补偿检查（简化示例，实际可更精细）
            for task in ready_batch:
                res = results[task.task_id]
                if res.status == TaskStatus.FAILED:
                    decision = await self.error_strategy.on_failure(task, res.error, context)
                    if decision.action == "compensate":
                        await self._compensate_chain(plan, results)
                        # 补偿后中断执行
                        pending.clear()
                        break
                    elif decision.action == "abort":
                        pending.clear()
                        break
                    # retry 由 Orchestrator 重新将任务加入 pending 或下一批次
                    elif decision.action == "retry":
                        pending.add(task.task_id)

        return results

    async def _execute_with_lifecycle(self, task: Task, context: ExecutionContext) -> TaskResult:
        agents = await self.registry.find(task.required_capability)
        if not agents:
            return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED,
                              error=ErrorInfo(type="NoAgent", message="No agent available", retryable=False))
        agent = agents[0]  # 需配合负载均衡
        return await agent.execute(task, context)

    async def _compensate_chain(self, plan: ExecutionPlan, results: dict[str, TaskResult]):
        # 按逆序调用已完成任务的补偿
        completed = [tid for tid, r in results.items() if r.status == TaskStatus.COMPLETED]
        for tid in reversed(completed):
            task = plan.tasks[tid]
            if task.task_type:
                # 从补偿注册表获取 handler
                handler = CompensationRegistry.get(task.task_type)
                if handler:
                    await handler.compensate(task, ExecutionContext(...))
```

### 9. 错误处理与补偿（可序列化）

```python
class ErrorStrategy(ABC):
    @abstractmethod
    async def on_failure(self, task: Task, error: ErrorInfo, context: ExecutionContext) -> "RetryDecision":
        ...

# 补偿注册表（替代嵌入 Task 的不可序列化对象）
class CompensationRegistry:
    _handlers: dict[str, "CompensationHandler"] = {}

    @classmethod
    def register(cls, task_type: str, handler: "CompensationHandler"):
        cls._handlers[task_type] = handler

    @classmethod
    def get(cls, task_type: str) -> Optional["CompensationHandler"]:
        return cls._handlers.get(task_type)

class CompensationHandler(ABC):
    @abstractmethod
    async def compensate(self, task: Task, context: ExecutionContext) -> None:
        ...
```

### 10. AgentRegistry 正式化

```python
class AgentRegistry(ABC):
    @abstractmethod
    async def find(self, requirement: CapabilityRequirement) -> list[BaseAgent]:
        """按能力需求查找匹配的 Agent 列表（已考虑健康状态、并发槽位）。"""
        ...

    @abstractmethod
    async def get_healthy_agents(self) -> list[BaseAgent]:
        ...

    @abstractmethod
    async def get_availability(self, agent: BaseAgent) -> int:
        """返回 Agent 当前可用的并发槽位。"""
        ...
```

### 11. 递归组合：`OrchestratorAsAgent` + `ResultAggregator`

```python
class ResultAggregator(ABC):
    @abstractmethod
    async def aggregate(self, results: dict[str, TaskResult], context: ExecutionContext) -> Any:
        """将子任务结果聚合为单个输出。"""
        ...

class OrchestratorAsAgent(BaseAgent):
    def __init__(
        self,
        orchestrator: Orchestrator,
        capability_spec: CapabilitySpec,
        aggregator: ResultAggregator
    ):
        self._orchestrator = orchestrator
        self._capability = capability_spec
        self._aggregator = aggregator

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        sub_results = await self._orchestrator.run(task.goal, context)
        output = await self._aggregator.aggregate(sub_results, context)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

---

## 修正后的架构全景

```
┌──────────────────────────────────────────────────────────────┐
│                        Orchestrator                           │
│  (并发执行循环: get_ready_batch → gather → 处理 handoff/错误) │
│                                                              │
│  依赖: ControlStrategy, AgentRegistry, MessageBus,           │
│        ErrorStrategy, CompensationRegistry                    │
└───────┬──────────────────────────────────────────────────────┘
        │
        ▼
  ControlStrategy (抽象)
     ├── SupervisorStrategy (LLM 动态规划)
     ├── WorkflowStrategy (静态 DAG)
     ├── HandoffStrategy (监听 bus + 解析 handoff)
     └── CouncilStrategy (并行相同任务 + 裁决)
        │
        ▼
  BaseAgent (抽象)
     ├── 普通 Agent (ReAct / ToolAgent)
     └── OrchestratorAsAgent (递归组合，内含 ResultAggregator)
        │
        ▼
  MessageBus (纯通信，标准消息类型)  ↔  ExecutionContext (分层状态)
```

---

## 总结

经过两轮架构评审与修正，现在的接口方案具备以下核心特征：

- **并发原生**：执行循环支持任务组并行，Council 和 Supervisor 的并行子任务不再需要 hack。
- **控制权声明化**：`HandoffRequest` 作为 `TaskResult` 的一部分，将 Handoff 语义暴露给 Orchestrator 而不破坏循环。
- **计划生命周期内聚**：`initialize_plan` 让策略自行决定如何生成计划，消除外部传参与动态生成的矛盾。
- **递归组合可落地**：`OrchestratorAsAgent` 与 `ResultAggregator` 明确了分解与聚合的契约。
- **生产就绪**：补偿注册表解决序列化问题，AgentRegistry 提供路由基础，标准消息类型保障通信契约。

这套抽象已经可以在 HpAgent 中作为核心契约直接进入实现阶段。





# 第三轮评审（mimo v2.5 pro）

前两轮修的是骨架断裂。这一轮的问题性质变了——骨架接上了，但关节的运动范围、肌肉的发力方向还有几处不协调，会导致实现阶段反复返工。

---

## 一、执行循环的实际运行逻辑有三处硬伤

### 1. Handoff 生成的新 Task 在本批次就被丢弃了——但下一批也会丢

看循环中的这段：

```python
if result.handoff_request:
    handoff_task = Task(...)
    plan.tasks[handoff_task.task_id] = handoff_task
    pending.add(handoff_task.task_id)
```

紧接着往下：

```python
for task in ready_batch:
    res = results[task.task_id]
    if res.status == TaskStatus.FAILED:
        decision = await self.error_strategy.on_failure(...)
        if decision.action == "compensate":
            await self._compensate_chain(...)
            pending.clear()    # ← handoff 刚加进来的 task 被清掉了
            break
```

原始 Task 的状态是 `COMPLETED`（它成功了，并且提出了 handoff 请求），不会触发 error path，所以这个具体场景没问题。但问题在更深处：

**原始 Task 的 status 到底是什么？** Agent A 成功完成了自己的工作，同时提出了 handoff。它的 `TaskResult.status` 应该是 `COMPLETED` 还是 `HANDED_OFF`？当前代码中没有任何逻辑检查 `handoff_request` 与 `status` 的一致性。如果一个 Agent 返回了 `status=FAILED` 且 `handoff_request=Some(...)`，系统会怎样？会同时触发错误补偿和 handoff 注入——这几乎必然是 bug。

**修正**：Handoff 语义上是一种 **特殊的完成状态**，要么新增 `TaskStatus.HANDED_OFF`，要么在循环中对 `handoff_request` 和 `status` 的组合做校验：

```python
# 最简洁的方案：在循环中强制校验
if result.handoff_request and result.status == TaskStatus.FAILED:
    raise InvalidStateError("Cannot handoff on a failed task")

if result.handoff_request:
    result.status = TaskStatus.HANDED_OFF  # 覆写状态，语义明确
    handoff_task = Task(...)
    plan.tasks[handoff_task.task_id] = handoff_task
    pending.add(handoff_task.task_id)
```

---

### 2. `get_ready_batch` 的返回值对 Council 模式存在语义歧义

Council 模式的本质是：**同一个 goal，N 个 Agent 各自独立执行一次，然后裁决**。

但 `get_ready_batch` 返回 `list[Task]`——Council 策略需要返回 N 个 `task_id` 不同、`goal` 相同、`required_capability` 不同的 Task。Orchestrator 循环拿到这 N 个 Task 后 `asyncio.gather` 并行执行，没问题。

**但结果怎么聚合？** 循环结束后 `results` 里有 N 个独立的 `TaskResult`，谁来决定最终采用哪个？

当前设计中，`on_batch_completed` 是 `-> None`——它只能 **修改 plan**，不能 **修改结果**。Council 的裁决需要从 N 个结果中选出最终答案，这个裁决结果必须被写入某个地方，但当前循环结构不支持"批次执行完毕后产生一个新的聚合结果"。

```python
async def on_batch_completed(
    self,
    results: dict[str, TaskResult],
    plan: ExecutionPlan,
    context: ExecutionContext
) -> None:   # ← 返回 None，裁决结果无处安放
    pass
```

Council 可以在 `on_batch_completed` 里向 `results` 字典里塞一个新条目（利用 Python dict 的引用传递），但这是一个隐式的副作用约定，没有任何接口约束。

**修正**：让 `on_batch_completed` 显式返回一组"注入结果"：

```python
@dataclass
class BatchOutcome:
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    tasks_to_remove: set[str] = field(default_factory=set)
    should_terminate: bool = False

class ControlStrategy(ABC):
    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: ExecutionPlan,
        context: ExecutionContext
    ) -> BatchOutcome:
        return BatchOutcome()  # 默认无操作
```

Council 的实现：

```python
async def on_batch_completed(self, results, plan, context) -> BatchOutcome:
    council_task_ids = [tid for tid in results if self._is_council_task(tid)]
    council_results = {tid: results[tid] for tid in council_task_ids}
    
    verdict = await self._judge(council_results, context)  # 裁决 LLM
    
    final_id = f"{self._council_name}_final"
    return BatchOutcome(
        injected_results={final_id: TaskResult(task_id=final_id, status=TaskStatus.COMPLETED, output=verdict)},
        tasks_to_remove=set(council_task_ids),  # 清理中间结果，或保留用于审计
        should_terminate=True,
    )
```

Orchestrator 循环消费它：

```python
outcome = await self.strategy.on_batch_completed(results, plan, context)
results.update(outcome.injected_results)
pending -= outcome.tasks_to_remove
if outcome.should_terminate:
    break
```

---

### 3. 错误补偿发生在 `on_batch_completed` 之前——顺序冲突

当前循环中：

```python
# 先处理 handoff 注入
for task, result in zip(ready_batch, batch_results):
    ...  # handoff 注入

# 然后调策略回调
await self.strategy.on_batch_completed(results, plan, context)

# 最后补偿检查
for task in ready_batch:
    if res.status == TaskStatus.FAILED:
        ...  # 补偿或终止
```

问题：

- **补偿和策略回调的顺序**：如果 `on_batch_completed` 需要看到所有结果（包括失败的）才能做决策，但它在补偿检查之前运行——而补偿检查可能会 `pending.clear()` 终止一切。这意味着策略回调可能执行了，但它的修改被后续的补偿终止覆盖了。
- **Council 的场景**：Council 的一个投票 Agent 失败了。`on_batch_completed` 需要决定"少数派失败怎么处理"。但如果补偿检查先清空了 pending，策略回调根本不会被调用。
- **Handoff 冲突**：一个批次中的 Agent A handoff 了，Agent B 失败了。当前代码先注入 handoff task，再做补偿检查。如果补偿触发 terminate，handoff task 刚刚被注入就被清掉了。

**根本原因**：循环体中错误处理和策略回调的执行顺序是一个需要明确语义的契约，但当前代码没有定义清楚。

**修正**：将补偿决策也委托给策略，让 `BatchOutcome` 统一承载所有批后决策：

```python
@dataclass
class BatchOutcome:
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    tasks_to_remove: set[str] = field(default_factory=set)
    should_terminate: bool = False
    failed_tasks_to_retry: set[str] = field(default_factory=set)

# Orchestrator 循环中的批后处理变成单一路径：
outcome = await self.strategy.on_batch_completed(results, plan, context)
results.update(outcome.injected_results)
pending -= outcome.tasks_to_remove
pending |= outcome.failed_tasks_to_retry
if outcome.should_terminate:
    break
```

补偿逻辑也从 Orchestrator 循环中移出，变成 Strategy 的职责（或 Strategy 内部委托给 ErrorStrategy）：

```python
class WorkflowControlStrategy(ControlStrategy):
    async def on_batch_completed(self, results, plan, context) -> BatchOutcome:
        outcome = BatchOutcome()
        for tid, res in results.items():
            if res.status == TaskStatus.FAILED:
                decision = await self.error_strategy.on_failure(...)
                if decision.action == "compensate":
                    outcome.should_terminate = True
                    # 标记需要补偿的任务，Orchestrator 或 Strategy 负责执行
                elif decision.action == "retry":
                    outcome.failed_tasks_to_retry.add(tid)
        return outcome
```

这样整个循环体变成纯粹的 **调度引擎**，不包含任何策略判断逻辑：

```python
while pending:
    ready_batch = await self.strategy.get_ready_batch(results, plan, pending, self.bus, context)
    if not ready_batch:
        break

    batch_results = await asyncio.gather(...)

    # 写入结果 + handoff 注入（纯机械操作）
    for task, result in zip(ready_batch, batch_results):
        results[task.task_id] = result
        pending.discard(task.task_id)
        if result.handoff_request:
            handoff_task = self._create_handoff_task(task, result)
            plan.tasks[handoff_task.task_id] = handoff_task
            pending.add(handoff_task.task_id)

    # 所有策略决策统一在此
    outcome = await self.strategy.on_batch_completed(results, plan, context)
    results.update(outcome.injected_results)
    pending -= outcome.tasks_to_remove
    pending |= outcome.failed_tasks_to_retry
    if outcome.should_terminate:
        break
```

---

## 二、接口级别的结构性缺陷

### 4. `HandoffRequest` 的语义是"从一个 Agent 发出的"，但 `MessageBus` 上也有 `HandoffMessage`

现在存在两条 Handoff 通道：

- **直接通道**：`TaskResult.handoff_request` —— Agent 通过返回值声明
- **间接通道**：Agent 在 `execute()` 内部调用 `bus.send(HandoffMessage(...))`

两种方式都能触发 handoff，但处理逻辑完全不同：

- 直接通道在 Orchestrator 循环中被处理（当前已实现）
- 间接通道需要某个组件在监听 bus——但谁在听？Orchestrator 的循环是 `while pending`，期间没有并行的 bus listener

这意味着 `MessageBus.listen()` 在当前架构中 **实际上没有消费者**。HandoffStrategy 的"监听 bus"只能在 `get_ready_batch` 或 `on_batch_completed` 里被动检查，不能实时响应。

**这是一个架构性的未解问题**。要么：

- **方案 A**：只保留直接通道（`TaskResult.handoff_request`），从 MessageBus 接口中删除 handoff 语义，Agent 不能在执行过程中主动 handoff，只能返回时声明。这是最简洁的方案，但限制了 Agent 的能力。
- **方案 B**：Orchestrator 在执行批次的同时启动一个后台 bus listener，用 `asyncio.gather` 合并执行和监听。增加复杂度，但保留 Agent 的主动权。
- **方案 C**：在 HandoffControlStrategy 中，`get_ready_batch` 阻塞等待 bus 消息（而非立即返回），此时批次执行是空的，Orchestrator 退化为事件循环。这与当前批次调度模型冲突。

**建议选方案 A**，因为 Handoff 的触发点天然在"Agent 完成当前任务之后"，执行过程中真正的 handoff 极少——如果有，通常是 Agent 内部的子工具切换，不应暴露到编排层。

---

### 5. `CompensationRegistry` 是全局单例，与 Orchestrator 的递归组合冲突

```python
class CompensationRegistry:
    _handlers: dict[str, "CompensationHandler"] = {}

    @classmethod
    def register(cls, task_type: str, handler: "CompensationHandler"):
        cls._handlers[task_type] = handler
```

当 `OrchestratorAsAgent` 递归组合时，内层 Orchestrator 的 `task_type` 与外层可能冲突——"compensate_analysis" 在内层和外层的补偿逻辑完全不同，但全局注册表只有一份。

**修正**：补偿注册表应该是每个 Orchestrator 实例的局部状态，通过依赖注入传入：

```python
class Orchestrator:
    def __init__(self, strategy, registry, bus, error_strategy, compensation_registry=None):
        self.compensation_registry = compensation_registry or CompensationRegistry()
```

---

### 6. `ExecutionPlan.conditions` 仍然只是一个 `dict[str, Any]`

条件分支是 Workflow 模式的核心能力之一，但 `conditions` 的类型是 `Any`，没有任何消费逻辑，也没有接口约束。在第三轮修正后如果还不给出明确语义，它在实现阶段会成为一个"人人都跳过"的字段。

**最小修正**：至少定义条件的结构和求值接口：

```python
@dataclass
class BranchCondition:
    source_task_id: str                    # 触发条件的任务
    predicate: Callable[[TaskResult], bool]  # 判断函数
    true_target: str                        # 条件为真时的下一个 task_id
    false_target: Optional[str] = None      # 条件为假时的下一个 task_id

@dataclass
class ExecutionPlan:
    tasks: dict[str, Task]
    dependencies: dict[str, list[str]]
    branches: list[BranchCondition] = field(default_factory=list)  # 替代 conditions
```

WorkflowControlStrategy 在 `get_ready_batch` 中检查 `branches`，根据已完成任务的结果决定路由。

注意：`predicate` 的可序列化问题与 CompensationHandler 类似。如果要在 Temporal 中持久化 plan，predicate 需要改为字符串标识符 + 注册表模式。

---

## 三、实现层面的隐患

### 7. `asyncio.gather` + `return_exceptions=True` 会吞掉真正的 bug

```python
batch_results = await asyncio.gather(
    *[self._execute_with_lifecycle(task, context) for task in ready_batch],
    return_exceptions=True
)
```

`return_exceptions=True` 会把所有异常转为 `Exception` 对象放进结果列表。但如果异常是 `BaseException` 子类（如 `KeyboardInterrupt`、`SystemExit`），它也会被吞掉并转为正常结果。

更隐蔽的问题：如果 `_execute_with_lifecycle` 内部有 bug（比如属性拼写错误导致 `AttributeError`），它会静默地变成一个 `TaskResult(status=FAILED)`，日志中不会有任何 traceback。

**修正**：

```python
import logging

for task, result in zip(ready_batch, batch_results):
    if isinstance(result, BaseException) and not isinstance(result, Exception):
        raise result  # KeyboardInterrupt 等直接抛出
    if isinstance(result, Exception):
        logging.exception(f"Task {task.task_id} raised unexpected exception", exc_info=result)
        result = TaskResult(
            task_id=task.task_id,
            status=TaskStatus.FAILED,
            error=ErrorInfo(type=type(result).__name__, message=str(result), retryable=False)
        )
    results[task.task_id] = result
```

---

### 8. `_execute_with_lifecycle` 中的 `agents[0]` 选 agent 的策略完全缺失

```python
async def _execute_with_lifecycle(self, task, context):
    agents = await self.registry.find(task.required_capability)
    if not agents:
        return TaskResult(...FAILED...)
    agent = agents[0]  # ← 随机选第一个？
    return await agent.execute(task, context)
```

当有多个 Agent 匹配同一能力时，选择逻辑应该在 AgentRegistry 中完成（它知道健康状态和并发槽位），而不是让 Orchestrator 拿到一个列表后盲目选第一个。`find()` 应该返回最佳匹配，而非候选列表。

---

### 9. Orchestrator 的 trace 传播

`ExecutionContext.trace_id` 在整个循环中被传给所有 Agent，但 `TaskResult` 中没有携带 trace 信息。当一个批次中有 10 个并行 Task，外部监控系统无法将某个慢 Task 的 trace 关联到对应的 Orchestrator 执行批次。

**修正**：在 Task 执行时自动注入 span context，或者在 TaskResult 中携带 trace 标识：

```python
@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None
    metrics: Optional[ExecutionMetrics] = None
    handoff_request: Optional[HandoffRequest] = None
    trace_id: Optional[str] = None  # 执行时的 trace，可能与 context.trace_id 不同（子 trace）
```

---

## 总结：第三轮问题矩阵

| 优先级 | 问题                                                   | 性质                    | 修正成本            |
| :----- | :----------------------------------------------------- | :---------------------- | :------------------ |
| **P0** | HandoffRequest 与 status 的一致性无校验                | 运行时必触发的 bug      | 低                  |
| **P0** | on_batch_completed 返回 None，Council 裁决结果无处安放 | Council 模式不可用      | 中                  |
| **P0** | 补偿与策略回调的执行顺序冲突                           | 复合场景下行为不确定    | 中                  |
| **P1** | 两条 Handoff 通道共存，bus 通道无消费者                | 架构歧义                | 低（选方案 A 即可） |
| **P1** | CompensationRegistry 全局单例与递归组合冲突            | 递归场景必崩            | 低                  |
| **P1** | conditions 无类型定义和消费逻辑                        | Workflow 条件分支不可用 | 中                  |
| **P2** | gather + return_exceptions 吞掉 BaseException          | 真 bug 被隐藏           | 低                  |
| **P2** | agent 选择策略 `[0]`                                   | 负载均衡完全缺失        | 低                  |
| **P2** | TaskResult 缺少 trace 传播                             | 可观测性断裂            | 低                  |

**整体判断**：这一轮的核心修复是 **让循环体变成纯机械调度引擎，把所有策略判断（包括补偿、裁决、终止决策）统一收口到 `BatchOutcome`**。做完这个改动后，Orchestrator 的循环就真正变成了一个与策略无关的执行骨架，四种模式只需实现各自的 Strategy 即可无侵入地插入。

剩下的 P1/P2 问题都是实现阶段能在一两天内补完的细节，不再构成架构层面的阻塞。

&nbsp;
&nbsp;


# 第四轮架构（deepseek v4 pro）

基于第三轮评审的精确指正，我对执行循环、策略接口和若干实现细节进行了**最后的结构性修正**。这一轮的核心目标是：**让 Orchestrator 退化为纯机械调度引擎，将所有策略判断（裁决、补偿、终止）统一收口到 `BatchOutcome`，并消除所有已发现的歧义与冲突。**

以下是最终修订后的完整方案。

---

## 最终版 Harness 抽象接口

### 1. 原子执行单元：`BaseAgent`

```python
from abc import ABC, abstractmethod
from typing import Optional

class BaseAgent(ABC):
    """原子执行单元，封装完整的 ReAct 循环或工具调用。"""

    @property
    @abstractmethod
    def capability(self) -> "CapabilitySpec":
        ...

    @abstractmethod
    async def execute(self, task: "Task", context: "ExecutionContext") -> "TaskResult":
        ...

    # 生命周期
    async def initialize(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def health_check(self) -> "AgentHealth": ...
    @property
    def max_concurrency(self) -> int: return 1
```

### 2. 任务与结果（增强）

```python
from dataclasses import dataclass, field
from enum import Enum

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"          # 新增：明确的移交完成状态
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"

@dataclass
class Task:
    task_id: str
    goal: str
    required_capability: "CapabilityRequirement"
    input_data: dict = field(default_factory=dict)
    task_type: str = "default"
    parent_task_id: Optional[str] = None

@dataclass
class ErrorInfo:
    type: str
    message: str
    retryable: bool
    partial_output: Any = None

@dataclass
class ExecutionMetrics:
    duration_ms: float = 0.0
    token_usage: dict = field(default_factory=dict)

@dataclass
class HandoffRequest:
    target_capability: "CapabilityRequirement"
    context_to_pass: dict
    reason: str

@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None
    metrics: Optional[ExecutionMetrics] = None
    handoff_request: Optional[HandoffRequest] = None
    trace_id: Optional[str] = None     # 新增：任务执行 trace
```

### 3. 能力声明与需求

```python
@dataclass
class CapabilitySpec:
    tags: set[str]
    priority: int = 0
    cost_tier: str = "default"

@dataclass
class CapabilityRequirement:
    required_tags: set[str]
    min_priority: int = 0
    max_cost: str = "premium"
    allow_degraded: bool = False
```

### 4. 分层上下文

```python
@dataclass
class SessionState:
    user_id: str
    conversation_history: list = field(default_factory=list)

@dataclass
class SharedMemory:
    namespace: dict = field(default_factory=dict)

@dataclass
class RuntimeConfig:
    timeout_seconds: int = 300
    max_retries: int = 3
    model_name: str = "default"

@dataclass
class ExecutionContext:
    session: SessionState
    shared_memory: SharedMemory
    config: RuntimeConfig
    trace_id: str
```

### 5. 通信层：纯管道 + 标准消息类型

```python
from dataclasses import dataclass
from typing import Union

@dataclass
class VoteMessage:
    msg_type: str = "vote"
    sender: str = ""
    task_id: str = ""
    verdict: Any = None
    confidence: float = 0.0

@dataclass
class StatusMessage:
    msg_type: str = "status"
    sender: str = ""
    task_id: str = ""
    status: str = ""

# 消息联合类型，不再包含 HandoffMessage（handoff 仅通过 TaskResult 声明）
Message = Union[VoteMessage, StatusMessage, dict]

class MessageBus(ABC):
    """纯通信管道，不包含任何编排语义。handoff 已从此层移除。"""
    @abstractmethod
    async def send(self, target_capability: str, message: Message) -> None: ...
    @abstractmethod
    async def broadcast(self, message: Message, capabilities: list[str] = None) -> list[Message]: ...
    @abstractmethod
    async def listen(self, agent_capability: str) -> "AsyncIterator[Message]": ...
```

### 6. 控制策略：`ControlStrategy`（最终版）

```python
from dataclasses import dataclass

@dataclass
class BatchOutcome:
    """批次完成后策略的统一反馈，承载所有决策。"""
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    tasks_to_remove: set[str] = field(default_factory=set)
    failed_tasks_to_retry: set[str] = field(default_factory=set)
    should_terminate: bool = False

class ControlStrategy(ABC):
    @abstractmethod
    async def initialize_plan(self, goal: str, context: ExecutionContext) -> "ExecutionPlan":
        ...

    @abstractmethod
    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: "ExecutionPlan",
        pending: set[str],
        bus: MessageBus,
        context: ExecutionContext
    ) -> list[Task]:
        """
        返回一批可并行执行的就绪任务。
        返回空列表表示当前无就绪任务（可能需等待或终止）。
        """
        ...

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: "ExecutionPlan",
        context: ExecutionContext
    ) -> BatchOutcome:
        """
        批次完成后的决策回调（裁决、补偿、终止、重试等）。
        默认无操作。
        """
        return BatchOutcome()
```

### 7. 编排计划：支持显式条件分支

```python
from typing import Callable, Optional

@dataclass
class BranchCondition:
    source_task_id: str
    predicate: str                     # 谓词标识符，由策略解释
    true_target: str
    false_target: Optional[str] = None

@dataclass
class ExecutionPlan:
    tasks: dict[str, Task]
    dependencies: dict[str, list[str]]
    branches: list[BranchCondition] = field(default_factory=list)
```

### 8. AgentRegistry：返回最佳匹配

```python
class AgentRegistry(ABC):
    @abstractmethod
    async def find_best(self, requirement: CapabilityRequirement) -> Optional[BaseAgent]:
        """基于能力、健康、并发槽位、成本返回最佳 Agent。"""
        ...

    @abstractmethod
    async def get_healthy_agents(self) -> list[BaseAgent]: ...

    @abstractmethod
    async def get_availability(self, agent: BaseAgent) -> int: ...
```

### 9. 错误处理与补偿（实例化注册表）

```python
class ErrorStrategy(ABC):
    @abstractmethod
    async def on_failure(self, task: Task, error: ErrorInfo, context: ExecutionContext) -> "RetryDecision":
        ...

class CompensationHandler(ABC):
    @abstractmethod
    async def compensate(self, task: Task, context: ExecutionContext) -> None: ...

class CompensationRegistry:
    """补偿处理器注册表，每个 Orchestrator 实例可拥有独立实例。"""
    def __init__(self):
        self._handlers: dict[str, CompensationHandler] = {}

    def register(self, task_type: str, handler: CompensationHandler):
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> Optional[CompensationHandler]:
        return self._handlers.get(task_type)
```

### 10. 纯机械调度引擎：`Orchestrator`

```python
import asyncio
import logging

class Orchestrator:
    def __init__(
        self,
        strategy: ControlStrategy,
        registry: AgentRegistry,
        bus: MessageBus,
        compensation_registry: Optional[CompensationRegistry] = None,
    ):
        self.strategy = strategy
        self.registry = registry
        self.bus = bus
        self.compensation_registry = compensation_registry or CompensationRegistry()

    async def run(self, goal: str, context: ExecutionContext) -> dict[str, TaskResult]:
        plan = await self.strategy.initialize_plan(goal, context)
        results: dict[str, TaskResult] = {}
        pending: set[str] = set(plan.tasks.keys())

        while pending:
            ready_batch = await self.strategy.get_ready_batch(
                results, plan, pending, self.bus, context
            )
            if not ready_batch:
                break

            # 并行执行
            batch_results = await asyncio.gather(
                *[self._execute_with_lifecycle(task, context) for task in ready_batch],
                return_exceptions=True
            )

            # 机械处理结果：记录状态，注入 handoff
            for task, raw in zip(ready_batch, batch_results):
                result = self._normalize_result(task, raw)
                results[task.task_id] = result
                pending.discard(task.task_id)

                # Handoff 语义校验与注入
                if result.handoff_request:
                    if result.status in (TaskStatus.FAILED,):
                        raise RuntimeError(f"Task {task.task_id} failed but requested handoff")
                    result.status = TaskStatus.HANDED_OFF
                    handoff_task = self._create_handoff_task(task, result)
                    plan.tasks[handoff_task.task_id] = handoff_task
                    pending.add(handoff_task.task_id)

            # 所有策略决策统一收口：裁决、补偿、重试、终止
            outcome = await self.strategy.on_batch_completed(results, plan, context)
            results.update(outcome.injected_results)
            pending -= outcome.tasks_to_remove
            pending |= outcome.failed_tasks_to_retry
            if outcome.should_terminate:
                break

            # 补偿处理：由 Strategy 在 on_batch_completed 中标记终止时，这里执行补偿
            if outcome.should_terminate:
                # 寻找需要补偿的任务并执行
                await self._execute_compensations(plan, results)

        return results

    async def _execute_with_lifecycle(self, task: Task, context: ExecutionContext) -> TaskResult:
        agent = await self.registry.find_best(task.required_capability)
        if not agent:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(type="NoAgent", message="No agent available", retryable=False)
            )
        return await agent.execute(task, context)

    def _normalize_result(self, task: Task, raw: Any) -> TaskResult:
        if isinstance(raw, BaseException) and not isinstance(raw, Exception):
            raise raw   # KeyboardInterrupt 等不吞
        if isinstance(raw, Exception):
            logging.exception(f"Task {task.task_id} crashed", exc_info=raw)
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(type=type(raw).__name__, message=str(raw), retryable=False)
            )
        return raw

    def _create_handoff_task(self, source_task: Task, result: TaskResult) -> Task:
        return Task(
            task_id=f"{source_task.task_id}_handoff",
            goal=result.handoff_request.reason,
            required_capability=result.handoff_request.target_capability,
            input_data=result.handoff_request.context_to_pass,
            parent_task_id=source_task.task_id,
        )

    async def _execute_compensations(self, plan: ExecutionPlan, results: dict[str, TaskResult]):
        # 逆序执行已完成任务的补偿
        completed = [tid for tid, r in results.items() if r.status == TaskStatus.COMPLETED]
        for tid in reversed(completed):
            task = plan.tasks.get(tid)
            if task and task.task_type:
                handler = self.compensation_registry.get(task.task_type)
                if handler:
                    await handler.compensate(task, ExecutionContext(...))
```

### 11. 递归组合：`OrchestratorAsAgent` + `ResultAggregator`

```python
class ResultAggregator(ABC):
    @abstractmethod
    async def aggregate(self, results: dict[str, TaskResult], context: ExecutionContext) -> Any: ...

class OrchestratorAsAgent(BaseAgent):
    def __init__(
        self,
        orchestrator: Orchestrator,
        capability_spec: CapabilitySpec,
        aggregator: ResultAggregator
    ):
        self._orchestrator = orchestrator
        self._capability = capability_spec
        self._aggregator = aggregator

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        sub_results = await self._orchestrator.run(task.goal, context)
        output = await self._aggregator.aggregate(sub_results, context)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

---

## 最终架构全景图

```
┌──────────────────────────────────────────────────────────────────┐
│                       Orchestrator (纯机械调度引擎)               │
│  while pending:                                                   │
│      batch = strategy.get_ready_batch(...)                        │
│      results = asyncio.gather(execute batch)                      │
│      处理 handoff 注入 (纯机械)                                   │
│      outcome = strategy.on_batch_completed(...)  ← 唯一决策收口   │
│      应用 outcome (注入结果、移除任务、重试、终止)                │
│      若终止 → 执行补偿链                                          │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
 ControlStrategy (抽象)
    ├── SupervisorStrategy (LLM 动态规划 → BatchOutcome 决定继续/终止)
    ├── WorkflowStrategy (静态 DAG + 分支 → BatchOutcome 含重试/补偿)
    ├── HandoffStrategy (get_ready_batch 从 plan/pending 中取 Handoff Task)
    └── CouncilStrategy (同一 goal 的 N 个 Task → on_batch_completed 裁决并注入结果)
       │
       ▼
 BaseAgent (抽象)
    ├── 普通 Agent (ReAct / ToolAgent)
    └── OrchestratorAsAgent (内含 ResultAggregator，实现递归组合)
       │
       ▼
基础设施:
   AgentRegistry (find_best) │ MessageBus (纯通信) │ ExecutionContext │ CompensationRegistry
```

---

## 第三轮修正带来的核心改进

| 改进点                 | 具体变化                                                     | 直接收益                                           |
| :--------------------- | :----------------------------------------------------------- | :------------------------------------------------- |
| **循环纯机械化**       | 所有策略判断（裁决、补偿、终止）移入 `BatchOutcome`          | Orchestrator 零策略逻辑，四种模式只需替换 Strategy |
| **Handoff 语义标准化** | 新增 `TaskStatus.HANDED_OFF`，强制校验；移除 MessageBus 上的 Handoff 通道 | 避免二义性，Agent 与 Orchestrator 交互清晰         |
| **Council 可落地**     | `on_batch_completed` 返回 `BatchOutcome`，支持注入裁决结果   | Council 模式无需 hack                              |
| **补偿与策略顺序统一** | 补偿由 Strategy 通过 `should_terminate` 触发，不再独立抢占   | 复合场景行为确定                                   |
| **递归组合无冲突**     | `CompensationRegistry` 实例化，非全局单例                    | 嵌套 Orchestrator 无副作用                         |
| **条件分支显式化**     | `BranchCondition` 替代裸 `dict`                              | Workflow 模式可消费                                |
| **异常处理安全**       | 区分 `BaseException`，记录异常日志                           | 真 bug 不被隐藏                                    |
| **Agent 选择优化**     | `registry.find_best` 返回最佳 Agent                          | 负载均衡与故障隔离                                 |
| **可观测性增强**       | `TaskResult` 携带 `trace_id`                                 | 关联监控链路                                       |

经过三轮评审，这套抽象接口已从“方向正确”演进为**结构完整、语义明确、可递归组合、生产就绪**的多智能体核心契约。它可以直接作为 HpAgent 重构的基础，支撑 Supervisor、Workflow、Handoff、Council 及其任意混合模式的实现。







# 第四轮评审 （mimo v2.5 pro）

三轮 P0 问题全部消除。这一轮的问题性质再次变化——不再是骨架断裂或语义矛盾，而是 **实现细节中的确定性 bug、隐式契约缺口、以及几个被拖了三轮仍未解决的残留设计洞**。

如果说前三轮是建筑师审查图纸，这一轮是结构工程师进场验房——梁柱没问题，但有几处管线走错了。

---

## 一、确定性 Bug（会在首次运行时触发）

### 1. 补偿逻辑是死代码

```python
            if outcome.should_terminate:
                break                          # ← 已跳出 while 循环

            # 补偿处理
            if outcome.should_terminate:       # ← 永远不可达
                await self._execute_compensations(plan, results)
```

第一个 `if` 执行 `break`，跳出 `while pending` 循环。第二个 `if` 在循环体外、`break` 之后，永远不会被执行。**补偿逻辑从未真正运行过。**

**修正**：把补偿移到 `break` 之前，或者更干净地移到循环结束后：

```python
        # --- while 循环结束 ---
        terminated = False
        while pending:
            ...
            outcome = await self.strategy.on_batch_completed(results, plan, context)
            results.update(outcome.injected_results)
            pending -= outcome.tasks_to_remove
            pending |= outcome.failed_tasks_to_retry
            if outcome.should_terminate:
                terminated = True
                break

        # 循环结束后统一处理补偿（无论正常结束还是终止）
        if terminated:
            await self._execute_compensations(plan, results)

        return results
```

---

### 2. `_execute_compensations` 中的 `ExecutionContext(...)` 是占位符

```python
await handler.compensate(task, ExecutionContext(...))
```

这里需要真实的 context，但方法签名里只有 `plan` 和 `results`，没有 `context`。实际运行时会直接抛 `TypeError`。

**修正**：

```python
async def _execute_compensations(self, plan: ExecutionPlan, results: dict[str, TaskResult], context: ExecutionContext):
    completed = [tid for tid, r in results.items() if r.status == TaskStatus.COMPLETED]
    for tid in reversed(completed):
        task = plan.tasks.get(tid)
        if task and task.task_type:
            handler = self.compensation_registry.get(task.task_type)
            if handler:
                try:
                    await handler.compensate(task, context)
                    results[tid] = TaskResult(task_id=tid, status=TaskStatus.COMPENSATED)
                except Exception as e:
                    logging.exception(f"Compensation failed for task {tid}", exc_info=e)
                    results[tid] = TaskResult(task_id=tid, status=TaskStatus.COMPENSATING,
                                              error=ErrorInfo(type="CompensationFailed", message=str(e), retryable=False))
```

---

## 二、缺失的基础设施（实现时会卡壳）

### 3. `BranchCondition.predicate: str` 无消费方

```python
@dataclass
class BranchCondition:
    source_task_id: str
    predicate: str                # "check_if_quality_above_threshold"
    true_target: str
    false_target: Optional[str] = None
```

这是一个字符串，但没有地方定义"如何把它解析成一个可执行的判断"。WorkflowControlStrategy 在 `get_ready_batch` 里拿到一个 `predicate="check_xxx"` 的条件，它怎么知道这个字符串代表什么逻辑？

到目前为止没有 `ConditionRegistry`，没有 `ConditionEvaluator` 接口。这个字段在实现阶段会变成要么硬编码 if-else，要么被忽略。

**修正**：补齐最小可用的条件求值接口：

```python
class ConditionEvaluator(ABC):
    @abstractmethod
    async def evaluate(self, predicate: str, task_result: TaskResult, context: ExecutionContext) -> bool:
        ...

# 注入到 WorkflowStrategy 中：
class WorkflowControlStrategy(ControlStrategy):
    def __init__(self, dag_definition: dict, evaluator: ConditionEvaluator):
        self.evaluator = evaluator
        ...
```

不复杂，但必须存在，否则条件分支是不可执行的声明。

---

### 4. `SharedMemory` 没有并发安全保障

```python
@dataclass
class SharedMemory:
    namespace: dict = field(default_factory=dict)
```

Orchestrator 的循环支持 `asyncio.gather` 并行执行多个 Agent。这些 Agent 可能同时读写 `context.shared_memory.namespace`。Python 的 `dict` 在 asyncio 单线程模型下不会出现数据损坏，但会出现 **逻辑竞态**：

- Agent A 读取 key="analysis_result"，发现不存在，准备写入
- Agent B 同时读取 key="analysis_result"，也发现不存在
- 两者都写入，后者覆盖前者

这在 Council 模式中尤为危险——多个 Agent 可能往同一命名空间写入投票结果。

**修正**：至少提供读写锁语义：

```python
class SharedMemory:
    def __init__(self):
        self._namespaces: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, namespace: str, key: str) -> Any:
        return self._namespaces.get(namespace, {}).get(key)

    async def set(self, namespace: str, key: str, value: Any) -> None:
        if namespace not in self._namespaces:
            self._namespaces[namespace] = {}
        self._namespaces[namespace][key] = value

    async def compare_and_set(self, namespace: str, key: str, expected: Any, new_value: Any) -> bool:
        """CAS 原语，防止竞态写入。"""
        current = await self.get(namespace, key)
        if current == expected:
            await self.set(namespace, key, new_value)
            return True
        return False
```

不需要很重，但需要有原子操作的语义。

---

### 5. 没有 Orchestrator 级别的超时

`RuntimeConfig.timeout_seconds` 在 `ExecutionContext` 里存在，但 `Orchestrator.run()` 从未消费它。如果一个批次的所有 Agent 都卡住了（比如 LLM 服务不可用），Orchestrator 会永远 `await asyncio.gather(...)`。

**修正**：

```python
# 在 while 循环内
try:
    batch_results = await asyncio.wait_for(
        asyncio.gather(
            *[self._execute_with_lifecycle(task, context) for task in ready_batch],
            return_exceptions=True
        ),
        timeout=context.config.timeout_seconds
    )
except asyncio.TimeoutError:
    logging.error(f"Batch execution timed out after {context.config.timeout_seconds}s")
    # 将超时的任务标记为失败，交给 Strategy 决定下一步
    for task in ready_batch:
        if task.task_id not in results:
            results[task.task_id] = TaskResult(
                task_id=task.task_id, status=TaskStatus.FAILED,
                error=ErrorInfo(type="Timeout", message="Batch execution timed out", retryable=True)
            )
            pending.discard(task.task_id)
    outcome = await self.strategy.on_batch_completed(results, plan, context)
    # ... 处理 outcome
    if outcome.should_terminate:
        terminated = True
        break
```

---

## 三、概念层面的残留模糊

### 6. `HandoffControlStrategy` 的实际职责是什么？

Handoff 的触发链是：

1. Agent 执行 → 返回 `TaskResult(handoff_request=...)`
2. Orchestrator 循环检测到 → 创建新 Task → 注入 plan 和 pending
3. 下一轮循环，Strategy 的 `get_ready_batch` 返回这个新 Task
4. 新的 Agent 执行

在这个流程中，**Handoff 的核心逻辑（识别移交意图、创建新任务）全在 Orchestrator 循环体内**。HandoffControlStrategy 的 `initialize_plan` 和 `get_ready_batch` 做的事情和其他 Strategy 几乎一样——初始化一个起始任务，返回就绪任务。

那 HandoffControlStrategy 的独特性到底在哪？它和 WorkflowControlStrategy 的区别是什么？如果唯一区别是"Handoff 可以动态增加任务到 plan 中"——但这也是 Supervisor 的能力。

**我的判断**：Handoff 不是一种独立的 ControlStrategy，而是一种 **Agent 行为模式**。任何 Strategy 下，只要 Agent 返回 `HandoffRequest`，Orchestrator 循环都会处理。"Handoff 模式"的特殊之处仅在于：Agent 的 prompt/system instruction 中被配置为"遇到超出能力范围的问题时主动移交"。

如果是这样，架构图中不应该有 `HandoffControlStrategy`，而应该有：

```
ControlStrategy
    ├── SupervisorStrategy
    ├── WorkflowStrategy
    └── CouncilStrategy

Agent 行为配置：
    └── handoff_enabled: bool  ← Agent 层面的配置，非 Strategy 层面
```

或者如果你坚持保留 HandoffStrategy，那它需要一个明确的 **独特行为定义**——比如"监听 MessageBus 上的移交请求而非从 plan 中取任务"。但第三轮中我们已经关闭了 Bus 上的 Handoff 通道。

---

### 7. Supervisor 动态添加任务的机制未显式化

Supervisor 的核心是 **LLM 看到当前结果后生成新的 Task**。`BatchOutcome` 有 `injected_results`（预计算的结果），但没有 `new_tasks`（需要 Agent 执行的新任务）。

Supervisor 实现者只能在 `on_batch_completed` 里直接修改 `plan.tasks` 和 `pending`（因为它们通过引用传入）：

```python
async def on_batch_completed(self, results, plan, context) -> BatchOutcome:
    # 调 LLM，基于当前 results 决定是否需要新任务
    new_tasks = await self._llm_plan_next(results, context)
    for task in new_tasks:
        plan.tasks[task.task_id] = task
        # 但 pending 是 set，这里拿不到引用
```

问题来了：**`pending` 不在 `on_batch_completed` 的参数里**。Supervisor 无法把新任务加入 pending。它只能修改 plan.tasks，但下一个循环中 `pending` 不包含这些新 task_id，所以它们永远不会被调度。

**这是又一个 P0 bug。**

**修正**：让 `BatchOutcome` 显式支持新任务注入：

```python
@dataclass
class BatchOutcome:
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    new_tasks: list[Task] = field(default_factory=list)          # ← 新增
    tasks_to_remove: set[str] = field(default_factory=set)
    failed_tasks_to_retry: set[str] = field(default_factory=set)
    should_terminate: bool = False
```

Orchestrator 消费时：

```python
outcome = await self.strategy.on_batch_completed(results, plan, context)
results.update(outcome.injected_results)
for new_task in outcome.new_tasks:
    plan.tasks[new_task.task_id] = new_task
    pending.add(new_task.task_id)                  # ← 关键
pending -= outcome.tasks_to_remove
pending |= outcome.failed_tasks_to_retry
```

---

### 8. OrchestratorAsAgent 的错误传播是盲的

```python
async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
    sub_results = await self._orchestrator.run(task.goal, context)
    output = await self._aggregator.aggregate(sub_results, context)
    return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

`sub_results` 中可能有 `FAILED` 状态的任务。但无论子结果如何，最终总是返回 `status=COMPLETED`。Aggregator 收到了一堆包含失败的结果，但没有关于"子任务失败了该怎么处理"的契约。

**修正**：

```python
async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
    sub_results = await self._orchestrator.run(task.goal, context)

    # 检查是否有子任务失败
    failed = [r for r in sub_results.values() if r.status == TaskStatus.FAILED]
    if failed and not self._allow_partial:
        return TaskResult(
            task_id=task.task_id, status=TaskStatus.FAILED,
            error=ErrorInfo(type="SubTaskFailed", message=f"{len(failed)} subtasks failed", retryable=False),
            output=sub_results  # 保留部分结果供调用方决策
        )

    output = await self._aggregator.aggregate(sub_results, context)
    return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

---

## 四、整体架构审视

走到第四轮，我来做一次全局判断——**哪些设计决策已经被验证为稳定，哪些还需要一轮确认**：

### 已稳定（可以开始实现）

| 组件                                       | 状态     | 说明                                  |
| :----------------------------------------- | :------- | :------------------------------------ |
| `BaseAgent`                                | **稳定** | 四轮未变，接口简洁完整                |
| `Task` / `TaskResult`                      | **稳定** | 状态枚举完整，HandoffRequest 定位清晰 |
| `CapabilitySpec` / `CapabilityRequirement` | **稳定** | 足够原型阶段使用                      |
| `AgentRegistry.find_best`                  | **稳定** | 正确的路由抽象                        |
| `CompensationRegistry`（实例化）           | **稳定** | 递归安全                              |
| `BatchOutcome` 模式                        | **稳定** | 但需要补充 `new_tasks` 字段           |
| Orchestrator 循环结构                      | **稳定** | 修正死代码和补充 timeout 后可用       |
| `MessageBus`（纯管道）                     | **稳定** | Handoff 语义已移除                    |

### 需要最后确认

| 组件                     | 悬而未决的问题                               |
| :----------------------- | :------------------------------------------- |
| `HandoffControlStrategy` | 是否独立存在？还是 Handoff 只是 Agent 行为？ |
| `BranchCondition`        | `predicate: str` 的求值接口缺失              |
| `SharedMemory`           | 并发安全语义                                 |
| `OrchestratorAsAgent`    | 错误传播策略                                 |
| Temporal 边界            | 哪些调用是 Activity，哪些是 Workflow 内联    |

---

## 最终修正清单

| #    | 问题                              | 严重程度 | 修正动作                              |
| :--- | :-------------------------------- | :------- | :------------------------------------ |
| 1    | 补偿逻辑死代码                    | **Bug**  | 移到循环结束后                        |
| 2    | `ExecutionContext(...)` 占位符    | **Bug**  | 补充参数传递                          |
| 3    | Supervisor 无法注入新任务         | **Bug**  | `BatchOutcome` 加 `new_tasks`         |
| 4    | `BranchCondition` 无求值接口      | **缺失** | 新增 `ConditionEvaluator`             |
| 5    | `SharedMemory` 无并发安全         | **缺失** | 加 CAS 原语或锁                       |
| 6    | 无 Orchestrator 级超时            | **缺失** | `asyncio.wait_for` 包裹批次执行       |
| 7    | `OrchestratorAsAgent` 错误传播    | **缺失** | 检查子任务失败状态                    |
| 8    | `HandoffControlStrategy` 定位模糊 | **概念** | 明确它是独立 Strategy 还是 Agent 行为 |

**结论**：修正完这 8 项后，接口层的审查可以结束。剩余的 Temporal 集成细节、具体 Strategy 实现、prompt 工程等都属于实现阶段的决策，不再需要在抽象层讨论。

&nbsp;
&nbsp;

# 第五轮架构 （deepseek v4 pro)

基于第四轮评审的精确指正，我对实现细节进行了彻底的工程化修复。所有确定性 bug、缺失的基础设施和概念模糊点均已消除。以下是最终可进入实现的完整方案。

---

## 最终版 Harness 抽象接口（第四轮修正后）

### 1. 原子执行单元：`BaseAgent`

```python
from abc import ABC, abstractmethod
from typing import Optional

class BaseAgent(ABC):
    """原子执行单元，封装完整的 ReAct 循环或工具调用。"""

    @property
    @abstractmethod
    def capability(self) -> "CapabilitySpec":
        ...

    @abstractmethod
    async def execute(self, task: "Task", context: "ExecutionContext") -> "TaskResult":
        ...

    # 生命周期
    async def initialize(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def health_check(self) -> "AgentHealth": ...
    @property
    def max_concurrency(self) -> int: return 1
```

### 2. 任务与结果（增强）

```python
from dataclasses import dataclass, field
from enum import Enum

class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"

@dataclass
class Task:
    task_id: str
    goal: str
    required_capability: "CapabilityRequirement"
    input_data: dict = field(default_factory=dict)
    task_type: str = "default"
    parent_task_id: Optional[str] = None

@dataclass
class ErrorInfo:
    type: str
    message: str
    retryable: bool
    partial_output: Any = None

@dataclass
class ExecutionMetrics:
    duration_ms: float = 0.0
    token_usage: dict = field(default_factory=dict)

@dataclass
class HandoffRequest:
    target_capability: "CapabilityRequirement"
    context_to_pass: dict
    reason: str

@dataclass
class TaskResult:
    task_id: str
    status: TaskStatus
    output: Any = None
    error: Optional[ErrorInfo] = None
    metrics: Optional[ExecutionMetrics] = None
    handoff_request: Optional[HandoffRequest] = None
    trace_id: Optional[str] = None
```

### 3. 能力声明与需求

```python
@dataclass
class CapabilitySpec:
    tags: set[str]
    priority: int = 0
    cost_tier: str = "default"

@dataclass
class CapabilityRequirement:
    required_tags: set[str]
    min_priority: int = 0
    max_cost: str = "premium"
    allow_degraded: bool = False
```

### 4. 分层上下文与并发安全

```python
import asyncio

@dataclass
class SessionState:
    user_id: str
    conversation_history: list = field(default_factory=list)

class SharedMemory:
    """支持命名空间隔离和并发安全的共享内存。"""
    def __init__(self):
        self._namespaces: dict[str, dict] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def get(self, namespace: str, key: str) -> Any:
        return self._namespaces.get(namespace, {}).get(key)

    async def set(self, namespace: str, key: str, value: Any) -> None:
        if namespace not in self._namespaces:
            self._namespaces[namespace] = {}
        self._namespaces[namespace][key] = value

    async def compare_and_set(self, namespace: str, key: str, expected: Any, new_value: Any) -> bool:
        """原子 CAS 操作，防止竞态写入。"""
        current = await self.get(namespace, key)
        if current == expected:
            await self.set(namespace, key, new_value)
            return True
        return False

@dataclass
class RuntimeConfig:
    timeout_seconds: int = 300
    max_retries: int = 3
    model_name: str = "default"

@dataclass
class ExecutionContext:
    session: SessionState
    shared_memory: SharedMemory
    config: RuntimeConfig
    trace_id: str
```

### 5. 通信层：纯管道 + 标准消息类型

```python
from typing import Union

@dataclass
class VoteMessage:
    msg_type: str = "vote"
    sender: str = ""
    task_id: str = ""
    verdict: Any = None
    confidence: float = 0.0

@dataclass
class StatusMessage:
    msg_type: str = "status"
    sender: str = ""
    task_id: str = ""
    status: str = ""

Message = Union[VoteMessage, StatusMessage, dict]

class MessageBus(ABC):
    @abstractmethod
    async def send(self, target_capability: str, message: Message) -> None: ...
    @abstractmethod
    async def broadcast(self, message: Message, capabilities: list[str] = None) -> list[Message]: ...
    @abstractmethod
    async def listen(self, agent_capability: str) -> "AsyncIterator[Message]": ...
```

### 6. 条件求值器

```python
class ConditionEvaluator(ABC):
    @abstractmethod
    async def evaluate(self, predicate: str, task_result: TaskResult, context: ExecutionContext) -> bool:
        ...
```

### 7. 控制策略：`ControlStrategy`（最终版）

```python
@dataclass
class BatchOutcome:
    """批次完成后策略的统一反馈，承载所有决策。"""
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    new_tasks: list[Task] = field(default_factory=list)          # Supervisor 动态注入新任务
    tasks_to_remove: set[str] = field(default_factory=set)
    failed_tasks_to_retry: set[str] = field(default_factory=set)
    should_terminate: bool = False

class ControlStrategy(ABC):
    @abstractmethod
    async def initialize_plan(self, goal: str, context: ExecutionContext) -> "ExecutionPlan":
        ...

    @abstractmethod
    async def get_ready_batch(
        self,
        results: dict[str, TaskResult],
        plan: "ExecutionPlan",
        pending: set[str],
        bus: MessageBus,
        context: ExecutionContext
    ) -> list[Task]:
        """
        返回一批可并行执行的就绪任务。
        空列表表示当前无就绪任务。
        """
        ...

    async def on_batch_completed(
        self,
        results: dict[str, TaskResult],
        plan: "ExecutionPlan",
        context: ExecutionContext
    ) -> BatchOutcome:
        """批次完成后的决策回调。"""
        return BatchOutcome()
```

**Handoff 的定位**：Handoff 不再作为独立的 `HandoffControlStrategy`，而是一种 **Agent 行为配置**。任何 Strategy 下，只要 Agent 返回 `HandoffRequest`，Orchestrator 都会自动注入新任务。Agent 的 system prompt 中可开启“遇到超出能力范围的问题时主动移交”选项，这是行为层而非控制流层的差异。

### 8. 编排计划：支持显式条件分支

```python
from typing import Optional

@dataclass
class BranchCondition:
    source_task_id: str
    predicate: str                     # 由 ConditionEvaluator 解析
    true_target: str
    false_target: Optional[str] = None

@dataclass
class ExecutionPlan:
    tasks: dict[str, Task]
    dependencies: dict[str, list[str]]
    branches: list[BranchCondition] = field(default_factory=list)
```

### 9. AgentRegistry：返回最佳匹配

```python
class AgentRegistry(ABC):
    @abstractmethod
    async def find_best(self, requirement: CapabilityRequirement) -> Optional[BaseAgent]: ...

    @abstractmethod
    async def get_healthy_agents(self) -> list[BaseAgent]: ...

    @abstractmethod
    async def get_availability(self, agent: BaseAgent) -> int: ...
```

### 10. 错误处理与补偿（实例化注册表，可序列化）

```python
class ErrorStrategy(ABC):
    @abstractmethod
    async def on_failure(self, task: Task, error: ErrorInfo, context: ExecutionContext) -> "RetryDecision": ...

class CompensationHandler(ABC):
    @abstractmethod
    async def compensate(self, task: Task, context: ExecutionContext) -> None: ...

class CompensationRegistry:
    """补偿处理器注册表，每个 Orchestrator 实例独立。"""
    def __init__(self):
        self._handlers: dict[str, CompensationHandler] = {}

    def register(self, task_type: str, handler: CompensationHandler):
        self._handlers[task_type] = handler

    def get(self, task_type: str) -> Optional[CompensationHandler]:
        return self._handlers.get(task_type)
```

### 11. 纯机械调度引擎：`Orchestrator`（最终版）

```python
import asyncio
import logging

class Orchestrator:
    def __init__(
        self,
        strategy: ControlStrategy,
        registry: AgentRegistry,
        bus: MessageBus,
        compensation_registry: Optional[CompensationRegistry] = None,
    ):
        self.strategy = strategy
        self.registry = registry
        self.bus = bus
        self.compensation_registry = compensation_registry or CompensationRegistry()

    async def run(self, goal: str, context: ExecutionContext) -> dict[str, TaskResult]:
        plan = await self.strategy.initialize_plan(goal, context)
        results: dict[str, TaskResult] = {}
        pending: set[str] = set(plan.tasks.keys())
        terminated = False

        while pending:
            ready_batch = await self.strategy.get_ready_batch(
                results, plan, pending, self.bus, context
            )
            if not ready_batch:
                break

            # 带超时的并行执行
            try:
                batch_results = await asyncio.wait_for(
                    asyncio.gather(
                        *[self._execute_with_lifecycle(task, context) for task in ready_batch],
                        return_exceptions=True
                    ),
                    timeout=context.config.timeout_seconds
                )
            except asyncio.TimeoutError:
                logging.error(f"Batch timed out after {context.config.timeout_seconds}s")
                batch_results = [
                    TaskResult(
                        task_id=task.task_id,
                        status=TaskStatus.FAILED,
                        error=ErrorInfo(type="Timeout", message="Batch execution timed out", retryable=True)
                    )
                    for task in ready_batch
                ]

            # 机械处理结果：状态记录 + handoff 注入
            for task, raw in zip(ready_batch, batch_results):
                result = self._normalize_result(task, raw)
                results[task.task_id] = result
                pending.discard(task.task_id)

                if result.handoff_request:
                    if result.status == TaskStatus.FAILED:
                        raise RuntimeError(f"Task {task.task_id} failed but requested handoff")
                    result.status = TaskStatus.HANDED_OFF
                    handoff_task = Task(
                        task_id=f"{task.task_id}_handoff",
                        goal=result.handoff_request.reason,
                        required_capability=result.handoff_request.target_capability,
                        input_data=result.handoff_request.context_to_pass,
                        parent_task_id=task.task_id,
                    )
                    plan.tasks[handoff_task.task_id] = handoff_task
                    pending.add(handoff_task.task_id)

            # 所有策略决策统一收口
            outcome = await self.strategy.on_batch_completed(results, plan, context)
            results.update(outcome.injected_results)
            for new_task in outcome.new_tasks:
                plan.tasks[new_task.task_id] = new_task
                pending.add(new_task.task_id)
            pending -= outcome.tasks_to_remove
            pending |= outcome.failed_tasks_to_retry
            if outcome.should_terminate:
                terminated = True
                break

        # 循环结束后执行补偿（如果有终止信号）
        if terminated:
            await self._execute_compensations(plan, results, context)

        return results

    async def _execute_with_lifecycle(self, task: Task, context: ExecutionContext) -> TaskResult:
        agent = await self.registry.find_best(task.required_capability)
        if not agent:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(type="NoAgent", message="No agent available", retryable=False)
            )
        return await agent.execute(task, context)

    def _normalize_result(self, task: Task, raw: Any) -> TaskResult:
        if isinstance(raw, BaseException) and not isinstance(raw, Exception):
            raise raw   # KeyboardInterrupt 等不吞
        if isinstance(raw, Exception):
            logging.exception(f"Task {task.task_id} crashed", exc_info=raw)
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(type=type(raw).__name__, message=str(raw), retryable=False)
            )
        return raw

    async def _execute_compensations(self, plan: ExecutionPlan, results: dict[str, TaskResult],
                                     context: ExecutionContext):
        completed = [tid for tid, r in results.items() if r.status == TaskStatus.COMPLETED]
        for tid in reversed(completed):
            task = plan.tasks.get(tid)
            if task and task.task_type:
                handler = self.compensation_registry.get(task.task_type)
                if handler:
                    try:
                        await handler.compensate(task, context)
                        results[tid] = TaskResult(task_id=tid, status=TaskStatus.COMPENSATED)
                    except Exception as e:
                        logging.exception(f"Compensation failed for task {tid}", exc_info=e)
                        results[tid] = TaskResult(
                            task_id=tid,
                            status=TaskStatus.COMPENSATING,
                            error=ErrorInfo(type="CompensationFailed", message=str(e), retryable=False)
                        )
```

### 12. 递归组合：`OrchestratorAsAgent` + `ResultAggregator`

```python
class ResultAggregator(ABC):
    @abstractmethod
    async def aggregate(self, results: dict[str, TaskResult], context: ExecutionContext) -> Any: ...

class OrchestratorAsAgent(BaseAgent):
    def __init__(
        self,
        orchestrator: Orchestrator,
        capability_spec: CapabilitySpec,
        aggregator: ResultAggregator,
        allow_partial: bool = False
    ):
        self._orchestrator = orchestrator
        self._capability = capability_spec
        self._aggregator = aggregator
        self._allow_partial = allow_partial

    @property
    def capability(self) -> CapabilitySpec:
        return self._capability

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        sub_results = await self._orchestrator.run(task.goal, context)

        failed = [r for r in sub_results.values() if r.status == TaskStatus.FAILED]
        if failed and not self._allow_partial:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=ErrorInfo(type="SubTaskFailed", message=f"{len(failed)} subtasks failed", retryable=False),
                output=sub_results
            )

        output = await self._aggregator.aggregate(sub_results, context)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

---

## 最终架构全景图

```
┌──────────────────────────────────────────────────────────────────┐
│                    Orchestrator (纯机械调度引擎)                  │
│  while pending:                                                    │
│      batch = strategy.get_ready_batch(...)                         │
│      results = asyncio.wait_for(gather(batch), timeout=...)        │
│      处理 handoff 注入 (纯机械)                                    │
│      outcome = strategy.on_batch_completed(...)  ← 唯一决策收口   │
│      应用 outcome (new_tasks, 移除, 重试, 终止)                   │
│  循环结束后按需执行补偿                                           │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
 ControlStrategy (抽象)
    ├── SupervisorStrategy (LLM 动态规划，通过 BatchOutcome.new_tasks 注入新任务)
    ├── WorkflowStrategy (静态 DAG + BranchCondition，依赖 ConditionEvaluator)
    └── CouncilStrategy (同一 goal 的 N 个 Task → on_batch_completed 裁决并注入结果)
    (Handoff 是 Agent 行为模式，由任意 Strategy 下的 Agent 通过 HandoffRequest 触发)
       │
       ▼
 BaseAgent (抽象)
    ├── 普通 Agent (ReAct / ToolAgent, 可配置 handoff_enabled)
    └── OrchestratorAsAgent (内含 ResultAggregator，递归组合，支持部分失败)
       │
       ▼
基础设施:
   AgentRegistry.find_best │ MessageBus (纯通信) │ ExecutionContext (含 SharedMemory 并发安全)
   CompensationRegistry (实例化) │ ConditionEvaluator │ ErrorStrategy
```

---

## 第四轮修正带来的关键改进

| 改进点                    | 具体变化                                                | 直接收益                    |
| :------------------------ | :------------------------------------------------------ | :-------------------------- |
| **补偿逻辑修复**          | 移到循环结束后执行，修正死代码和 context 占位符         | 补偿机制真正可用            |
| **超时机制**              | Orchestrator 级 `asyncio.wait_for` 包裹批次             | 防止无限等待                |
| **Supervisor 动态注入**   | `BatchOutcome.new_tasks`，Orchestrator 将其加入 pending | Supervisor 模式可工作       |
| **条件分支可执行**        | 新增 `ConditionEvaluator` 接口，`WorkflowStrategy` 消费 | Workflow 条件分支不再为声明 |
| **SharedMemory 并发安全** | 提供 CAS 原语和原子操作                                 | Council 等并行模式安全      |
| **递归组合错误传播**      | `OrchestratorAsAgent` 检查子任务失败，支持部分成功      | 递归组合的鲁棒性            |
| **Handoff 定位澄清**      | 移除独立的 HandoffControlStrategy，作为 Agent 行为配置  | 消除架构歧义                |

经过四轮严格评审，这套抽象接口已具备**结构完整、语义明确、并发安全、可递归组合、生产就绪**的全部特征，可以直接作为 HpAgent 多智能体架构的基础契约进入工程实现阶段。

&nbsp;
&nbsp;

# 第五轮评审（mimo v2.5 pro）

先给一个明确判断：**这套架构已经可以进入实现了。**

四轮评审下来，P0 级设计缺陷全部消除。当前版本的核心循环是正确的，接口正交性是完整的，四种模式的承载能力已经从理论可行变成了工程可操作。继续在抽象层打磨的边际收益已经低于直接写代码的收益。

但"可以实现"不等于"完美"。以下是 **最后一组实现前必须知道的问题**——不再是架构缺陷，而是代码落地时会咬你的东西。

---

## 一、两个确定性 Bug

### 1. 补偿遗漏了 HANDED_OFF 任务

```python
completed = [tid for tid, r in results.items() if r.status == TaskStatus.COMPLETED]
```

Agent A 完成了工作并移交了控制权，产生了副作用。终止时需要回滚。但 `HANDED_OFF` 不在筛选条件中，它的副作用不会被补偿。

```python
compensatable = [
    tid for tid, r in results.items()
    if r.status in (TaskStatus.COMPLETED, TaskStatus.HANDED_OFF)
]
```

---

### 2. 超时后已完成的任务结果被丢弃

```python
except asyncio.TimeoutError:
    batch_results = [
        TaskResult(task_id=task.task_id, status=TaskStatus.FAILED, ...)
        for task in ready_batch
    ]
```

如果批次中有 5 个任务，3 个在超时前已完成，2 个仍在运行。超时触发后，5 个全部被覆盖为 FAILED。那 3 个已完成的结果丢失了。

**修正**：用 `asyncio.Task` 替代裸协程，超时后检查哪些已完成：

```python
futures = [
    asyncio.ensure_future(self._execute_with_lifecycle(task, context))
    for task in ready_batch
]
try:
    await asyncio.wait_for(asyncio.gather(*futures, return_exceptions=True),
                           timeout=context.config.timeout_seconds)
except asyncio.TimeoutError:
    logging.error(f"Batch timed out after {context.config.timeout_seconds}s")
    for future in futures:
        if not future.done():
            future.cancel()

batch_results = []
for task, future in zip(ready_batch, futures):
    if future.done() and not future.cancelled():
        try:
            batch_results.append(future.result())
        except Exception as e:
            batch_results.append(e)
    else:
        batch_results.append(TaskResult(
            task_id=task.task_id, status=TaskStatus.FAILED,
            error=ErrorInfo(type="Timeout", message="Timed out", retryable=True)
        ))
```

---

## 二、三个实现前必须明确的契约

### 3. `pending` 与 `results` 的一致性规则

当一个 task 被 retry（加入 `failed_tasks_to_retry`）时，它重新进入 `pending`。但它的旧 FAILED 结果仍在 `results` 中。下次 `get_ready_batch` 被调用时，`results` 中该 task 的状态是 FAILED。

策略实现者需要知道这个行为——否则 Supervisor 策略可能看到 `results` 中有 FAILED 就认为整个流程需要终止，而实际上该任务正在被重试。

**建议**：在 `Orchestrator.run()` 的文档字符串中明确以下不变量：

> - `pending` 中的 task_id **不会** 出现在 `results` 中，除非该 task 处于重试状态（FAILED + 在 `failed_tasks_to_retry` 中）
> - `results` 中的 task_id **不会** 出现在 `pending` 中，除非该 task 被标记为重试
> - `get_ready_batch` 收到的 `results` 包含所有已执行任务的最新结果（包括失败的）

### 4. `shared_memory` 在递归组合时的可见性

`OrchestratorAsAgent.execute()` 将同一个 `context`（包括同一个 `SharedMemory`）传给内层 `orchestrator.run()`。这意味着内层所有 Agent 与外层所有 Agent 共享同一块内存。

这可能是期望行为（全局共享），也可能是危险行为（内层子任务污染外层状态）。没有接口级别的控制。

**建议**：提供 `SharedMemory.fork(namespace_prefix: str) -> SharedMemory`，返回一个隔离视图。默认行为可以是共享，但需要显式隔离时有手段：

```python
class SharedMemory:
    async def fork(self, prefix: str) -> "SharedMemory":
        """返回一个隔离的子内存，写入时自动加前缀，读取可选穿透到父内存。"""
        ...
```

不急于在 V1 实现，但接口定义中应该预留这个方法的位置。

---

### 5. `BranchCondition.predicate` 的调度时机

`ConditionEvaluator.evaluate()` 在 `get_ready_batch` 中被调用（由 WorkflowStrategy 负责）。但 evaluate 的参数是 `TaskResult`——如果有多个 source task 的结果需要组合判断，当前签名只能传入一个 result。

```python
async def evaluate(self, predicate: str, task_result: TaskResult, context: ExecutionContext) -> bool:
```

如果条件是"task A 的置信度 > 0.8 AND task B 的输出包含关键字"，单个 `task_result` 不够。

**修正**：签名改为接收所有已完成结果，让 evaluator 自行提取所需：

```python
async def evaluate(self, predicate: str, results: dict[str, TaskResult], context: ExecutionContext) -> bool:
```

---

## 三、架构成熟度评估

走到这里，我来做一个诚实的整体评估：

### 四轮迭代中被正确解决的核心问题

| 轮次   | 问题                                            | 解法                                                      |
| :----- | :---------------------------------------------- | :-------------------------------------------------------- |
| 第一轮 | 控制流模型混杂、Bus 职责膨胀、不可组合          | 引入 ControlStrategy，Bus 回归哑管道，OrchestratorAsAgent |
| 第二轮 | 串行循环、Plan 生命周期矛盾、Handoff 无表达手段 | 并发任务组、initialize_plan、HandoffRequest 声明化        |
| 第三轮 | 死代码、顺序冲突、概念歧义                      | BatchOutcome 统一收口、Handoff 降级为 Agent 行为          |
| 第四轮 | 补偿死代码、超时缺失、并发安全、条件求值        | 全部修复（本轮）                                          |

### 当前版本的设计质量评级

| 维度                | 评级 | 说明                                                         |
| :------------------ | :--- | :----------------------------------------------------------- |
| **接口正交性**      | A    | Agent / Task / Strategy / Bus / Context 五个维度完全解耦     |
| **模式覆盖**        | A-   | Supervisor、Workflow、Council 明确支撑；Handoff 作为 Agent 行为合理但缺少独立控制的开关 |
| **可组合性**        | A    | OrchestratorAsAgent + ResultAggregator + 实例化 CompensationRegistry |
| **并发安全**        | B+   | SharedMemory 有 CAS，但 fork/隔离机制未实现                  |
| **错误处理**        | B+   | 补偿链完整，但 HANDED_OFF 遗漏（本轮修正）                   |
| **Temporal 就绪度** | B    | 接口不阻止 Temporal 集成，但具体边界需要实现阶段确定         |
| **可测试性**        | A    | 所有组件通过 ABC 抽象，Mock 友好                             |

### 实现优先级建议

```
Phase 1 (核心骨架，1-2 周):
  ├── BaseAgent + Task + TaskResult 数据结构
  ├── Orchestrator.run() 循环（含超时、handoff、补偿）
  ├── WorkflowControlStrategy（最简单的策略，静态 DAG）
  └── AgentRegistry 内存实现 + InMemoryMessageBus

Phase 2 (策略扩展，1-2 周):
  ├── SupervisorControlStrategy（接入 LLM 规划）
  ├── CouncilControlStrategy（并行 + 裁决）
  ├── BranchCondition + ConditionEvaluator
  └── OrchestratorAsAgent + ResultAggregator

Phase 3 (生产化，2-3 周):
  ├── SharedMemory 持久化 + fork 隔离
  ├── Temporal Activity/Workflow 边界划分
  ├── Agent 生命周期管理（健康检查、并发控制）
  └── 可观测性（trace 传播、metrics 收集）
```

---

## 最终结论

**抽象层的审查到此结束。**

这套设计从第一轮的"方向正确但无法运行"，经过四轮迭代，演化为一个"结构完整、语义明确、有清晰实现路径"的架构契约。它不是完美的——`SharedMemory` 隔离、`ConditionEvaluator` 多源求值、Temporal 边界划分都需要在实现阶段继续细化——但它足以作为 HpAgent 重构的工程蓝图。

最后一句建议：**不要试图一次实现所有四种策略**。先用 WorkflowControlStrategy 把 Orchestrator 循环跑通，验证整个骨架的正确性，再逐步叠加 Supervisor 和 Council。骨架验证越早，后续返工越少。

&nbsp;
&nbsp;

# 架构总结文档（doubao expert mod）

## 一、核心背景：要解决什么问题？
为`HpAgent`框架设计**通用多Agent协作架构**，目标是同时支持4种经典协作模式，且支持任意嵌套组合（例如"总管模式里套流程模式，流程里套移交模式"）：

| 协作模式 | 通俗解释 |
|---------|---------|
| **Supervisor（总管模式）** | 一个"总管LLM"负责拆解任务，动态分配给不同子Agent执行 |
| **Workflow（流程模式）** | 按预定义的DAG（有向无环图）串行/并行执行固定流程任务 |
| **Handoff（移交模式）** | Agent执行过程中遇到超出能力范围的问题时，主动将任务移交给其他Agent |
| **Council（议会模式）** | 同一个任务并行发给多个Agent独立执行，最后由"议长LLM"投票裁决最终结果 |

**核心诉求**：底层Agent能力复用 + 上层协作模式可插拔 + 支持复杂嵌套组合 + 生产级可靠性。

---

## 二、架构演变：从"能跑"到"生产可用"的3轮关键迭代
### 阶段1：初始设计（第一轮选择）—— 搭骨架但埋了7个硬伤
**核心思路**：抽象"三大接口+一个总线"，试图用一套统一接口覆盖所有4种模式：
- `BaseAgent`：原子执行单元（干具体活）
- `BaseOrchestrator`：编排器（派活的），通过派生不同子类实现4种模式
- `Task`：任务数据结构（描述要做什么）
- `MessageBus`：通信总线（Agent间传递消息）

**致命问题（评审揪出的P0级漏洞）**：

1. **控制流错配**：Handoff是"Agent驱动的移交"，但被强行塞进"Orchestrator驱动的调度"逻辑，导致`HandoffOrchestrator`变成空壳
2. **总线职责膨胀**：将"移交（handoff）"这种编排语义塞进纯通信的MessageBus，总线从"哑管道"变成"智能路由器"
3. **不可组合**：Orchestrator不是Agent，无法被上层编排（例如Supervisor里不能直接调用Workflow）
4. **Task语义混杂**：同时包含"能力需求"（要什么Agent）和"DAG依赖"（要等哪些任务），职责不清
5. **状态黑洞**：用无类型的`global_context: dict`存储所有状态，无隔离、无契约、无持久化
6. **错误处理简陋**：只有字符串错误信息，无重试、回滚、补偿机制
7. **Agent无生命周期**：缺少启动、关闭、健康检查、并发控制等生产必需能力

---

### 阶段2：第一轮修改 —— 解耦核心问题，修复P0级漏洞
**核心改动（救架构根基）**：
1. **新增`ControlStrategy`（控制策略）**：将"决定下一步派什么活"的决策逻辑从Orchestrator中完全抽离，Orchestrator退化为纯执行循环。4种模式仅需替换不同的`ControlStrategy`实现
2. **瘦身MessageBus**：只保留`send/broadcast/listen`纯通信接口，handoff逻辑移到策略层
3. **实现`OrchestratorAsAgent`适配器**：将任何Orchestrator包装成Agent，支持递归嵌套组合
4. **拆分`Task`与`ExecutionPlan`**：Task只描述"做什么"，ExecutionPlan专门描述"任务依赖关系"
5. **分层状态管理**：将`global_context`拆分为`SessionState`（用户会话）、`SharedMemory`（Agent间共享）、`RuntimeConfig`（运行时配置）
6. **增强错误处理**：结构化`ErrorInfo`，新增`ErrorStrategy`处理失败决策，引入`CompensationHandler`支持回滚
7. **补全Agent生命周期**：增加`initialize/shutdown/health_check`接口和`max_concurrency`并发控制

---

### 阶段3：第二轮评审修正 —— 补执行模型的漏洞
**新暴露的核心问题**：执行循环是串行的，无法支持Council的并行语义和Handoff的"执行中移交"语义。

**最终修正**：
1. **重构为批量并行执行循环**：Orchestrator每次获取一批可并行执行的任务，通过`asyncio.gather`异步执行，完美适配Council的"多Agent并行投票"场景
2. **显式支持Handoff语义**：在`TaskResult`中新增`handoff_request`字段，Agent执行中可声明"要移交"，Orchestrator自动将其转换为新任务加入执行计划
3. **动态生成执行计划**：将Plan初始化交给`ControlStrategy`负责（Supervisor调LLM动态生成Plan，Workflow加载预定义DAG）
4. **统一决策收口**：新增`BatchOutcome`数据结构，所有策略决策（裁决、重试、终止、注入新任务）都通过它返回给Orchestrator

---

## 三、最终架构：核心组件与职责（通俗版）
把架构想象成一个"多Agent协作的指挥系统"，每个组件的角色和职责清晰划分：

| 组件名称 | 通俗角色 | 核心职责 |
|---------|---------|---------|
| **`BaseAgent`** | 一线执行员工 | 只干具体活（写代码、查数据、调用工具），有明确的能力标签，支持启动、健康检查、关闭 |
| **`Task`** | 标准化工作单 | 只描述"要做什么"，标注所需能力，不包含任何编排依赖 |
| **`ExecutionPlan`** | 工作计划 | 描述任务之间的依赖关系和条件分支，不管由谁来执行 |
| **`ControlStrategy`** | 决策参谋 | 决定下一步派哪些活：<br>- Supervisor：问LLM生成新任务<br>- Workflow：按预定义流程选任务<br>- Council：把一个活派给多个人<br>- Handoff：处理员工的移交请求 |
| **`Orchestrator`** | 执行总指挥 | 纯机械调度引擎，按参谋的决策执行：<br>1. 获取一批可并行的任务<br>2. 派给对应能力的员工<br>3. 收集结果、处理失败和移交<br>4. 重复直到任务完成或终止 |
| **`MessageBus`** | 内部对讲机 | 纯通信管道，只负责传递消息，不掺和任何决策或编排 |
| **`ExecutionContext`** | 指挥中心信息面板 | 分层存储全局数据：<br>- 会话状态：用户ID、对话历史<br>- 共享内存：Agent间共享的中间结果（带并发安全）<br>- 运行时配置：超时、重试、模型选择 |
| **`OrchestratorAsAgent`** | 外包团队负责人 | 将一整个指挥系统包装成"超级员工"，能被更高层的指挥系统调度（实现嵌套组合的核心） |
| **`ErrorStrategy`** | 应急处理员 | 任务失败时，决定是重试、跳过、回滚还是终止整个流程 |
| **`CompensationRegistry`** | 回滚专员 | 管理不同类型任务的补偿逻辑，流程终止时按逆序执行回滚 |

### 最终架构的核心特性
1. **可插拔**：切换4种协作模式仅需替换`ControlStrategy`，底层Agent、总线等组件完全不用修改
2. **可嵌套**：任何编排逻辑都能包装成Agent，支持任意深度的组合（例如"Supervisor里套Council，Council里套Workflow"）
3. **并行原生**：执行循环天然支持批量并行，无需hack即可实现Council等并行场景
4. **生产就绪**：内置生命周期管理、错误补偿、状态隔离、超时控制、可观测性（耗时、Token统计）

---

## 四、可复用的架构设计经验（通用且值钱）
1. **正交分离是架构的生命线**
   组件必须只干一件事（通信归通信，编排归编排，执行归执行）。改通信逻辑不会影响编排，改编排不会影响执行，测试和维护成本会指数级下降。

2. **控制流与执行必须解耦**
   把"决定做什么"（策略）和"怎么做"（执行）拆分开，才能用同一套执行引擎支持多种不同的业务逻辑。这是所有可扩展系统的核心设计原则。

3. **数据结构要纯粹，语义要单一**
   一个数据结构只承载一种语义（Task只描述"做什么"，Plan描述"怎么做"）。如果一个数据结构同时承载多种语义，使用方的逻辑必然会混乱。

4. **并发模型必须匹配业务语义**
   如果业务需要并行（例如议会模式），执行循环就不能设计成串行的。强行在串行循环里实现并行，只会把逻辑藏到黑盒里，导致架构变形。

5. **可组合性优先于一切**
   设计组件时首先要想："这个组件能否被当作一个更大系统的一部分来使用？"。Orchestrator必须能包装成Agent，这是支持复杂系统的基础。

6. **原型和生产的差距在"非功能需求"**
   原型只需要"能跑"，但生产级系统必须补全：生命周期管理、错误处理、状态管理、超时控制、可观测性。这些是90%的bug来源。

7. **架构是迭代出来的，不是设计出来的**
   不要追求一步到位的完美架构。先搭最小可用骨架，然后通过评审找漏洞，再逐步重构。每一轮迭代解决一个核心问题，最终会得到一个健壮且贴合实际需求的架构。

