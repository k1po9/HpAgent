# HpAgent 多Agent架构最终迭代指导文档

> **状态**: 抽象层审查完成,可进入工程实现
> **迭代**: 2个AI模型 x 5轮攻防推演 x 15+处架构硬伤修复

---

## 零、架构哲学：四字真言

```
Agent 是动作  |  Task 是数据  |  Orchestrator 是调度  |  Strategy 是策略  |  Bus 是脉络
```

一切设计决策围绕这五句话展开。偏离任一原则,必然在后续迭代中被评审暴击。

---

## 一、目标：同时支撑四种协作模式 + 自由嵌套

| 模式 | 通俗描述 | 核心差异 |
|------|---------|---------|
| **Supervisor** (总管) | LLM 动态拆解任务,分配子Agent | 计划动态生成 |
| **Workflow** (流程) | 按预定义 DAG 串行/并行/条件分支 | 计划预定义,静态 DAG |
| **Handoff** (移交) | Agent 完成工作后主动移交控制权 | **Agent 行为**,非独立 Strategy |
| **Council** (议会) | 同一任务并行发给 N 个Agent,裁决定结果 | 并行相同任务 + 聚合 |

---

## 二、最终确定的核心架构(第五轮)

### 架构全景图

```
┌──────────────────────────────────────────────────────────────────┐
│                    Orchestrator (纯机械调度引擎)                  │
│  while pending:                                                    │
│      batch = strategy.get_ready_batch(...)                         │
│      results = asyncio.wait_for(gather(batch), timeout=...)        │
│      处理 handoff 注入 (纯机械操作)                                │
│      outcome = strategy.on_batch_completed(...)  ← 唯一决策收口   │
│      应用 outcome (new_tasks, 移除, 重试, 终止)                   │
│  循环结束后按需执行补偿                                           │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
 ControlStrategy (抽象)
    ├── SupervisorStrategy (LLM 动态规划, 通过 BatchOutcome.new_tasks 注入)
    ├── WorkflowStrategy (静态 DAG + BranchCondition + ConditionEvaluator)
    └── CouncilStrategy (同一 goal 的 N 个 Task → on_batch_completed 裁决)
    (Handoff 是 Agent 行为, 由任意 Strategy 下的 Agent 返回 HandoffRequest 触发)
       │
       ▼
 BaseAgent (抽象)
    ├── 普通 Agent (ReAct / ToolAgent, 可配置 handoff_enabled)
    └── OrchestratorAsAgent (内含 ResultAggregator, 支持递归组合和部分失败)
       │
       ▼
基础设施:
   AgentRegistry.find_best │ MessageBus (纯通信) │ ExecutionContext
   CompensationRegistry (实例化) │ ConditionEvaluator │ ErrorStrategy
```

### 核心组件职责速查

| 组件 | 一句话职责 | 稳定状态 |
|------|-----------|---------|
| `BaseAgent` | 原子执行单元,封装 ReAct 循环 + 工具调用 | **稳定** |
| `Task` | 纯粹的"做什么"声明,不含编排依赖 | **稳定** |
| `TaskResult` | 包含 handoff/error/metrics/trace 的标准化结果 | **稳定** |
| `ExecutionPlan` | 任务图: tasks + dependencies + branches | **稳定** |
| `ControlStrategy` | 策略接口: initialize_plan + get_ready_batch + on_batch_completed | **稳定** |
| `Orchestrator` | 纯机械调度引擎: 循环取批→并行执行→收口决策 | **稳定** |
| `MessageBus` | 纯哑管道: send/broadcast/listen,不包含编排语义 | **稳定** |
| `BatchOutcome` | 决策反馈: injected_results + new_tasks + 移除/重试/终止 | **稳定** |
| `ExecutionContext` | 分层状态: session + shared_memory + config | **稳定** |
| `AgentRegistry` | 按能力+健康+并发槽位返回最佳Agent | **稳定** |
| `CompensationRegistry` | 实例化补偿处理器注册表(递归安全) | **稳定** |
| `OrchestratorAsAgent` | 将Orchestrator包装为Agent实现递归组合 | **稳定** |
| `ConditionEvaluator` | 条件分支求值接口 | **稳定** |
| `ResultAggregator` | 子任务结果聚合接口 | **稳定** |
| `ErrorStrategy` | 失败决策: 重试/跳过/补偿/终止 | **稳定** |

---

## 三、五轮迭代中解决的关键问题(防踩坑清单)

### P0 级: 不改会崩的致命问题

| # | 问题 | 根因 | 解法 | 对应的错误思维 |
|---|------|------|------|--------------|
| 1 | Handoff 控制流不属 Orchestrator | Agent-driven routing 被强行塞进 Orchestrator-driven scheduling | Handoff 降级为 Agent 行为,通过 `TaskResult.handoff_request` 声明 | 为了统一入口强行把不同控制流模型塞进同一接口 |
| 2 | Orchestrator 不可组合 | `BaseOrchestrator` 不是 `BaseAgent` | `OrchestratorAsAgent` 适配器 | 设计组件时不考虑"它能否被更大系统使用" |
| 3 | MessageBus 长出手脚 | `handoff()` 是编排语义,被放进通信层 | Bus 回归 `send/broadcast/listen` 纯管道 | 职责膨胀: 哑管道变智能路由器 |
| 4 | 串行循环无法承载 Council/并行 | `decide_next` 返回单个 Task,循环体串行 await | 改为 `get_ready_batch` 返回 Task 列表 + `asyncio.gather` 并行 | 执行模型不匹配业务语义 |
| 5 | Plan 生命周期矛盾 | `run(plan)` 要求外部传入 Plan,但 Supervisor 需要动态生成 | `initialize_plan(goal)` 由 Strategy 负责,Orchestrator 只接收 goal | 接口设计不考虑所有实现的差异 |
| 6 | 补偿逻辑是死代码 | `break` 之后写补偿,永远不可达 | 补偿移到 while 循环结束后,用 `terminated` 标志 | 写完循环不检查控制流 |
| 7 | Supervisor 无法注入新任务 | `on_batch_completed` 拿不到 `pending` 引用 | `BatchOutcome.new_tasks` 显式支持 | 隐式副作用 + 引用传递 = 不确定行为 |

### P1 级: 会导致工程妥协的严重问题

| # | 问题 | 解法 |
|---|------|------|
| 8 | Task 职责混杂(能力路由 + DAG 依赖) | Task 只保留 `required_capability`,DAG 移到 `ExecutionPlan.dependencies` |
| 9 | `global_context: dict` 无类型/无隔离 | 拆为 `SessionState` + `SharedMemory`(带 CAS 并发安全) + `RuntimeConfig` |
| 10 | 错误处理仅有 error string | 结构化 `ErrorInfo` + `ErrorStrategy` + `CompensationHandler` + `CompensationRegistry` |
| 11 | 两条 Handoff 通道共存(Bus + TaskResult) | 删除 Bus 上的 Handoff 通道,统一用 `TaskResult.handoff_request` |
| 12 | CompensationRegistry 全局单例与递归冲突 | 改为实例化,每个 Orchestrator 拥有独立实例 |
| 13 | `ExecutionPlan.conditions` 无类型无消费逻辑 | 改为 `BranchCondition` 列表 + `ConditionEvaluator` 接口 |
| 14 | OrchestratorAsAgent 错误传播为盲 | 检查子任务失败状态,支持 `allow_partial` 参数 |
| 15 | Handoff 任务状态无校验 | 新增 `TaskStatus.HANDED_OFF`,强制校验 fail+handoff 冲突 |

### P2 级: 后续实现中必须补齐

| # | 问题 | 解法 |
|---|------|------|
| 16 | `return_exceptions=True` 吞掉 BaseException | 区分 `BaseException` 和 `Exception`,前者直接 raise |
| 17 | `agent = agents[0]` 盲目选第一个 | `AgentRegistry.find_best()` 返回最佳匹配而非列表 |
| 18 | 超时后已完成任务的结果被丢弃 | 用 `asyncio.Task` + `future.done()` 检查,保留已完成结果 |
| 19 | 补偿遗漏 HANDED_OFF 任务 | 筛选条件加入 `TaskStatus.HANDED_OFF` |
| 20 | TaskResult 缺少 trace 传播 | 加入 `trace_id` 字段 |
| 21 | SharedMemory 无递归隔离 | 预留 `fork(namespace_prefix)` 接口 |
| 22 | ConditionEvaluator 参数不完整 | 签名改为接收 `results: dict` 而非单个 `task_result` |

---

## 四、最终接口契约(可作为代码蓝本)

### 1. BaseAgent

```python
class BaseAgent(ABC):
    @property
    @abstractmethod
    def capability(self) -> "CapabilitySpec": ...

    @abstractmethod
    async def execute(self, task: "Task", context: "ExecutionContext") -> "TaskResult": ...

    # 生命周期
    async def initialize(self) -> None: ...
    async def shutdown(self) -> None: ...
    async def health_check(self) -> "AgentHealth": ...

    @property
    def max_concurrency(self) -> int:
        return 1
```

### 2. Task / TaskResult

```python
class TaskStatus(Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    HANDED_OFF = "handed_off"        # ← 明确的移交完成状态
    COMPENSATING = "compensating"
    COMPENSATED = "compensated"

@dataclass
class Task:
    task_id: str
    goal: str
    required_capability: "CapabilityRequirement"
    input_data: dict = field(default_factory=dict)
    task_type: str = "default"                       # 用于补偿注册表查找
    parent_task_id: Optional[str] = None              # 追踪子任务关系

@dataclass
class ErrorInfo:
    type: str
    message: str
    retryable: bool
    partial_output: Any = None

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
    handoff_request: Optional[HandoffRequest] = None  # ← Agent 移交意图
    trace_id: Optional[str] = None                     # ← 链路追踪
```

### 3. ControlStrategy + BatchOutcome

```python
@dataclass
class BatchOutcome:
    """策略决策的统一收口。"""
    injected_results: dict[str, TaskResult] = field(default_factory=dict)
    new_tasks: list[Task] = field(default_factory=list)          # Supervisor 动态注入
    tasks_to_remove: set[str] = field(default_factory=set)
    failed_tasks_to_retry: set[str] = field(default_factory=set)
    should_terminate: bool = False

class ControlStrategy(ABC):
    @abstractmethod
    async def initialize_plan(self, goal: str, context: ExecutionContext) -> "ExecutionPlan": ...

    @abstractmethod
    async def get_ready_batch(
        self, results: dict[str, TaskResult], plan: "ExecutionPlan",
        pending: set[str], bus: MessageBus, context: ExecutionContext
    ) -> list[Task]: ...

    async def on_batch_completed(
        self, results: dict[str, TaskResult], plan: "ExecutionPlan",
        context: ExecutionContext
    ) -> BatchOutcome:
        return BatchOutcome()
```

### 4. Orchestrator(核心调度循环)

```python
class Orchestrator:
    def __init__(self, strategy: ControlStrategy, registry: AgentRegistry,
                 bus: MessageBus, compensation_registry=None): ...

    async def run(self, goal: str, context: ExecutionContext) -> dict[str, TaskResult]:
        plan = await self.strategy.initialize_plan(goal, context)
        results: dict[str, TaskResult] = {}
        pending: set[str] = set(plan.tasks.keys())
        terminated = False

        while pending:
            ready_batch = await self.strategy.get_ready_batch(results, plan, pending, self.bus, context)
            if not ready_batch:
                break

            # 带超时的并行执行
            futures = [asyncio.ensure_future(self._execute_with_lifecycle(t, context)) for t in ready_batch]
            try:
                await asyncio.wait_for(
                    asyncio.gather(*futures, return_exceptions=True),
                    timeout=context.config.timeout_seconds)
            except asyncio.TimeoutError:
                for f in futures:
                    if not f.done():
                        f.cancel()

            # 机械处理结果: 状态记录 + handoff 注入(纯机械操作,不包含策略)
            for task, future in zip(ready_batch, futures):
                if future.done() and not future.cancelled():
                    try:
                        raw = future.result()
                    except Exception as e:
                        raw = e
                else:
                    raw = TaskResult(task_id=task.task_id, status=TaskStatus.FAILED,
                                     error=ErrorInfo(type="Timeout", message="Timed out", retryable=True))
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
                        parent_task_id=task.task_id)
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

        if terminated:
            await self._execute_compensations(plan, results, context)
        return results

    def _normalize_result(self, task, raw):
        if isinstance(raw, BaseException) and not isinstance(raw, Exception):
            raise raw  # KeyboardInterrupt 等不吞
        if isinstance(raw, Exception):
            logging.exception(f"Task {task.task_id} crashed", exc_info=raw)
            return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED,
                              error=ErrorInfo(type=type(raw).__name__, message=str(raw), retryable=False))
        return raw
```

### 5. 递归组合

```python
class ResultAggregator(ABC):
    @abstractmethod
    async def aggregate(self, results: dict[str, TaskResult], context: ExecutionContext) -> Any: ...

class OrchestratorAsAgent(BaseAgent):
    def __init__(self, orchestrator: Orchestrator, capability_spec: CapabilitySpec,
                 aggregator: ResultAggregator, allow_partial: bool = False):
        ...

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        sub_results = await self._orchestrator.run(task.goal, context)
        failed = [r for r in sub_results.values() if r.status == TaskStatus.FAILED]
        if failed and not self._allow_partial:
            return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED,
                              error=ErrorInfo(type="SubTaskFailed", ...), output=sub_results)
        output = await self._aggregator.aggregate(sub_results, context)
        return TaskResult(task_id=task.task_id, status=TaskStatus.COMPLETED, output=output)
```

---

## 五、关键设计原则(可复用,跨项目适用)

### 1. 正交分离是架构的生命线
组件必须只做一件事。通信归通信(Bus),编排归编排(Strategy),执行归执行(Agent)。改任何一层不影响其他层。

### 2. 控制流与执行必须解耦
把"决定做什么"(ControlStrategy)和"怎么做"(Orchestrator)拆开。同一套执行引擎支持多种策略。

### 3. 数据结构纯粹,语义单一
`Task` 只描述"做什么"，`ExecutionPlan` 描述"怎么做"。一个数据结构承载多种语义 = 使用方逻辑必然混乱。

### 4. 并发模型必须匹配业务语义
业务需要并行(Council) → 执行循环必须支持批量并行。强行在串行循环里实现并行 = 把逻辑藏进黑盒。

### 5. 可组合性优先于一切
设计每个组件时问:"它能否被当作更大系统的一部分?"。Orchestrator 必须能包装成 Agent。

### 6. 所有策略决策必须统一收口
`BatchOutcome` 是唯一的决策通道。分多个出口(补偿在这里,重试在那里) = 行为不确定。

### 7. 声明式优于隐式引用
`new_tasks` 放入 `BatchOutcome` 由 Orchestrator 消费,而不是 Strategy 直接修改 `pending` 引用。

### 8. 哑管道,不要智能管道
MessageBus 只管收发,不负责理解消息内容。Handoff 不是通信,是编排。

### 9. 全局单例是递归的敌人
CompensationRegistry 必须实例化,否则嵌套 Orchestrator 的 task_type 互相覆盖。

### 10. 原型和生产的差距在非功能需求
生命周期管理、错误补偿、状态隔离、超时控制、可观测性 —— 这些是 90% 生产 bug 的来源。

---

## 六、实施路线图

### Phase 1: 核心骨架 (1-2 周)
**目标**: 让 Orchestrator 循环跑通,验证设计正确性

```
├── BaseAgent + Task + TaskResult (数据结构)
├── Orchestrator.run() 循环 (含超时、handoff、补偿)
├── WorkflowControlStrategy (最简单的策略,静态 DAG)
├── AgentRegistry 内存实现 + InMemoryMessageBus
└── 单元测试覆盖核心循环
```

**关键验证点**: 用一个简单的 3-task 串行 DAG 把循环完整跑通

### Phase 2: 策略扩展 (1-2 周)
**目标**: 三种 Strategy 全部实现,递归组合可工作

```
├── SupervisorControlStrategy (接入 LLM 规划)
├── CouncilControlStrategy (并行 + 裁决)
├── BranchCondition + ConditionEvaluator
├── OrchestratorAsAgent + ResultAggregator
└── 集成测试: Supervisor 内嵌 Workflow 的嵌套场景
```

### Phase 3: 生产化 (2-3 周)
**目标**: 生产级可靠性

```
├── SharedMemory 持久化 + fork 隔离
├── Temporal Activity/Workflow 边界划分
│   (关键约束: LLM 调用必须在 Activity 中,非 Workflow 内联)
├── Agent 生命周期管理 (健康检查、并发控制)
├── 可观测性 (trace 传播、metrics 收集)
└── 压力测试 + 故障注入
```

### 设计质量自检清单

- [ ] `BaseAgent.execute()` 签名是否正确(task + context → TaskResult)
- [ ] 是否保证 Handoff 只能通过 `TaskResult.handoff_request` 触发(不走 Bus)
- [ ] 是否所有策略决策都通过 `BatchOutcome` 返回(不被 Orchestrator 内联判断)
- [ ] `CompensationRegistry` 是不是实例化的(不是全局单例)
- [ ] 补偿筛选是否包含 `HANDED_OFF` 状态的任务
- [ ] 超时处理后是否保留了已完成任务的结果
- [ ] `OrchestratorAsAgent` 是否检查了子任务失败状态
- [ ] `SharedMemory` 是否提供了并发安全原语(CAS 或锁)
- [ ] `ConditionEvaluator.evaluate()` 签名是否接收完整 results 而非单个 result
- [ ] `Task` 数据结构中是否没有 `dependencies` 字段(依赖属于 Plan,不属于 Task)

---

## 七、模式速查: 四种 Strategy 的实现要点

### SupervisorStrategy
- `initialize_plan`: 调用 LLM 根据 goal 生成初始 Plan
- `get_ready_batch`: 从 Plan 中返回所有无依赖任务
- `on_batch_completed`: LLM 审查结果 → 通过 `BatchOutcome.new_tasks` 注入新任务或 `should_terminate`

### WorkflowStrategy
- `initialize_plan`: 从预定义 DAG 配置加载 Plan(含 branch 条件)
- `get_ready_batch`: 返回所有依赖已满足 + 条件判断通过的任务
- `on_batch_completed`: 检查失败 → 通过 `BatchOutcome` 决策重试/补偿/终止

### CouncilStrategy
- `initialize_plan`: 为同一个 goal 创建 N 个 task(分别对应 N 个 Agent)
- `get_ready_batch`: 返回所有 N 个 task
- `on_batch_completed`: 调裁决 LLM → 通过 `BatchOutcome.injected_results` 注入最终结果 + `should_terminate=True`

### Handoff(非独立 Strategy)
- 任何 Strategy 下的 Agent 返回 `TaskResult(handoff_request=...)`
- Orchestrator 循环自动注入新 Task 到 plan 和 pending
- Agent 的 system prompt 中配置 `handoff_enabled: true`

---

## 八、文档版本历史

| 轮次 | 设计者 | 评审者 | 核心贡献 |
|------|--------|--------|---------|
| 第一轮选择 | deepseek v4 pro | - | 提出 Agent/Orchestrator/Task/Bus 四层分离 |
| 第一轮评审 | - | mimo v2.5 pro | 发现 10 处问题(P0 x3, P1 x4, P2 x3) |
| 第一轮修改 | deepseek v4 pro | - | 引入 ControlStrategy, 修复 P0 级问题 |
| 第二轮评审 | - | mimo v2.5 pro | 发现执行循环断裂(串行 vs 并行) |
| 第二轮修改 | deepseek v4 pro | - | 重构为并发任务组循环, 引入 HandoffRequest |
| 第三轮评审 | - | mimo v2.5 pro | 发现补偿死代码、顺序冲突、概念歧义 |
| 第三轮修改 | deepseek v4 pro | - | BatchOutcome 统一收口, Handoff 降级为 Agent 行为 |
| 第四轮评审 | - | mimo v2.5 pro | 发现 3 个 bug(补偿死代码/占位符/超时丢弃) + 2 个缺失 + 2 个概念问题 |
| 第四轮修改 | deepseek v4 pro | - | 全部修复,补全 ConditionEvaluator/SharedMemory 并发安全 |
| 第五轮评审 | - | mimo v2.5 pro | 最终确认: 可以进入实现; 指出 2 个残留 bug + 3 个实现前必须明确的契约 |
| 架构总结 | doubao expert mod | - | 从教学角度重新梳理,提炼可复用设计原则 |

**结论**: 经过 5 轮攻防推演, 15+ 处架构硬伤修复, 当前接口方案已达到 **结构完整、语义明确、并发安全、可递归组合、生产就绪** 的状态, 可直接作为 HpAgent 重构的工程蓝图。
