# Phase 1 → Phase 2 过渡计划

## Phase 1 成果回顾

### 已完成的组件

| 文件 | 内容 | 行数(估) | 测试 |
|------|------|---------|------|
| `src/agent/types.py` | 7 枚举 + 15 dataclass | ~120 | ✅ 18 用例 |
| `src/agent/interfaces.py` | 5 ABC (BaseAgent/ControlStrategy/AgentRegistry/MessageBus/ErrorStrategy) | ~90 | ✅ 8 用例 |
| `src/agent/context.py` | SharedMemory(并发安全) + ExecutionContext | ~80 | ✅ 12 用例 |
| `src/agent/orchestrator.py` | 纯机械调度引擎(批量并行+超时+handoff+补偿) | ~160 | ✅ 10 用例 |
| `src/agent/strategies.py` | WorkflowControlStrategy + ConditionEvaluator + ResultAggregator | ~130 | ✅ 17 用例 |
| `src/agent/bus.py` | InMemoryMessageBus | ~60 | ✅ 2 用例 |
| `src/agent/registry.py` | InMemoryAgentRegistry(find_best) | ~70 | ✅ 6 用例 |
| `src/agent/compensation.py` | CompensationRegistry(实例化) + CompensationHandler | ~30 | ✅ 3 用例 |
| `src/agent/errors.py` | 4 ErrorStrategy(Retry/Skip/Compensate/Abort) | ~45 | ✅ 6 用例 |
| `src/agent/adapters.py` | ReActAgent(HarnessRunner包装器) | ~60 | ✅ 3 用例 |
| `src/agent/__init__.py` | 统一导出 | ~50 | - |

**总计: 90 tests, 0 failures, 0 外部依赖, 0 现有代码修改**

### Phase 1 的架构位置

```
Orchestrator (纯机械调度引擎)
    │
    ▼
ControlStrategy (抽象)
    └── WorkflowControlStrategy ✅ (Phase 1 完成)
    ├── SupervisorStrategy ❌ (Phase 2)
    └── CouncilStrategy ❌ (Phase 2)
    
BaseAgent (抽象)
    └── ReActAgent ✅ (Phase 1 完成)
    └── OrchestratorAsAgent ❌ (Phase 2)
    
基础设施 (全部 ✅):
    AgentRegistry | MessageBus | ExecutionContext | CompensationRegistry
    ErrorStrategy (4种) | ConditionEvaluator | ResultAggregator
```

---

## Phase 2 目标

新增两种 Strategy + 递归组合机制，使四种模式全部可运行。

### Phase 2 交付物

| # | 组件 | 说明 | 复杂度 |
|---|------|------|--------|
| 1 | `SupervisorControlStrategy` | LLM 动态规划，通过 BatchOutcome.new_tasks 注入 | 中 |
| 2 | `CouncilControlStrategy` | 同 goal 创建 N 个 Task，并行执行后裁决 | 中 |
| 3 | `OrchestratorAsAgent` | 将 Orchestrator 包装为 BaseAgent，实现递归组合 | 低 |
| 4 | WorkflowStrategy 增强 | BranchCondition + ConditionEvaluator 在 get_ready_batch 中正确集成 | 低 |
| 5 | 集成测试 | Supervisor 内嵌 Workflow 的嵌套场景 | 中 |

### 不在此阶段做的

- 不接入真实 LLM（Supervisor/Council 的 LLM 调用用 mock/stub 代替）
- 不集成 Temporal
- 不修改 main.py
- 不做 SharedMemory 持久化

---

## 实施计划

### Step 1: `OrchestratorAsAgent` (递归组合)

**文件**: `src/agent/composite.py` (新建)

```python
class OrchestratorAsAgent(BaseAgent):
    """
    将任何 Orchestrator 包装为 Agent，使其可被上层编排调度。
    这是实现"Supervisor 内嵌 Workflow"的关键。
    """
    def __init__(
        self,
        orchestrator: Orchestrator,
        capability_spec: CapabilitySpec,
        aggregator: ResultAggregator,
        allow_partial: bool = False,
    ):
        ...

    async def execute(self, task: Task, context: ExecutionContext) -> TaskResult:
        # 1. 调用内层 Orchestrator.run(task.goal, context)
        # 2. 检查子任务失败状态 (allow_partial 控制)
        # 3. 通过 ResultAggregator 聚合结果
        # 4. 返回包装后的 TaskResult
```

**测试**: `test/test_agent/test_composite.py`
- 单层组合: OrchestratorAsAgent 被当作普通 Agent 执行
- 错误传播: 子任务失败 → OrchestratorAsAgent 返回 FAILED
- allow_partial: 部分失败时仍返回 COMPLETED

### Step 2: `SupervisorControlStrategy` (LLM 动态规划)

**文件**: `src/agent/strategies.py` (扩展)

```python
class SupervisorControlStrategy(ControlStrategy):
    """
    Supervisor 模式: LLM 动态拆解目标 → 生成子 Task → 根据结果决定下一步。

    核心流程:
      1. initialize_plan: 调用 LLM 生成初始 Task 列表
      2. get_ready_batch: 返回所有依赖已满足的 Task
      3. on_batch_completed: LLM 审查结果 → 通过 new_tasks 注入新任务或 should_terminate
    """

    def __init__(self, planner: "LLMPlanner", reviewer: "LLMReviewer | None" = None):
        self._planner = planner         # LLM 规划器接口
        self._reviewer = reviewer       # LLM 审查器接口（可选，复用 planner）

    async def initialize_plan(self, goal, context) -> ExecutionPlan:
        # 调用 planner.plan(goal, context) → (tasks, dependencies)
        ...

    async def on_batch_completed(self, results, plan, context) -> BatchOutcome:
        # 调用 reviewer.review(results, context) → 决策
        # 可能是: (continue, [new_tasks]) 或 (done, verdict)
        ...
```

**关键抽象**: `LLMPlanner` — 将 LLM 调用抽象为接口

```python
class LLMPlanner(ABC):
    """LLM 规划器接口 — Phase 2 提供 Stub 实现，Phase 3 接入真实 ResourcePool。"""
    @abstractmethod
    async def plan(self, goal: str, context: ExecutionContext) -> tuple[list[Task], dict[str, list[str]]]:
        """返回 (tasks, dependencies)。"""
        ...

class LLMReviewer(ABC):
    """LLM 审查器接口。"""
    @abstractmethod
    async def review(self, results: dict[str, TaskResult], context: ExecutionContext) -> tuple[bool, list[Task]]:
        """返回 (is_done, new_tasks)。"""
        ...
```

**测试**: `test/test_agent/test_supervisor.py`
- 单轮规划: LLM 返回 3 个 Task → 全部执行 → done
- 多轮规划: LLM 第1轮返回2个Task → 执行后 → 第2轮返回1个Task → done
- 提前终止: LLM 审查后认为不需要更多任务 → should_terminate

### Step 3: `CouncilControlStrategy` (并行投票)

**文件**: `src/agent/strategies.py` (扩展)

```python
class CouncilControlStrategy(ControlStrategy):
    """
    Council 模式: 同一个 goal 并行给 N 个 Agent，由 Judge LLM 裁决定结果。

    核心流程:
      1. initialize_plan: 为同一 goal 创建 N 个 Task (分别对应 N 个 Agent)
      2. get_ready_batch: 返回所有 N 个 Task
      3. on_batch_completed: Judge LLM 审查所有结果 → injected_results 注入裁决
    """

    def __init__(self, agents: list[str], judge: "LLMJudge"):
        self._agent_capabilities = agents    # N 个 Agent 的能力标签
        self._judge = judge                   # 裁决 LLM 接口

    async def initialize_plan(self, goal, context) -> ExecutionPlan:
        # 为同一 goal 创建 N 个 Task
        tasks = {}
        for tag in self._agent_capabilities:
            task_id = f"council_{tag}"
            tasks[task_id] = Task(
                task_id=task_id,
                goal=goal,
                required_capability=CapabilityRequirement(required_tags={tag}),
            )
        return ExecutionPlan(tasks=tasks)

    async def on_batch_completed(self, results, plan, context) -> BatchOutcome:
        # Judge LLM 裁决 → 选择最佳结果
        verdict = await self._judge.judge(results, context)
        return BatchOutcome(
            injected_results={
                "council_verdict": TaskResult(
                    task_id="council_verdict",
                    status=TaskStatus.COMPLETED,
                    output=verdict,
                )
            },
            should_terminate=True,
        )
```

**关键抽象**: `LLMJudge`

```python
class LLMJudge(ABC):
    """LLM 裁决器接口。"""
    @abstractmethod
    async def judge(self, results: dict[str, TaskResult], context: ExecutionContext) -> Any:
        """返回裁决结果（多数投票 / 最高置信度 / LLM 裁决）。"""
        ...
```

**测试**: `test/test_agent/test_council.py`
- 3 Agent 投票: 全部返回不同结果 → Judge 选出最佳
- 部分失败: 1/3 失败 → Judge 从2个成功结果中裁决
- 全部失败: 所有 Agent 失败 → 返回 FAILED

### Step 4: WorkflowStrategy 增强

**修改**: `src/agent/strategies.py` (修改 WorkflowControlStrategy.get_ready_batch)

```python
async def get_ready_batch(self, results, plan, pending, bus, context):
    for task_id in sorted(pending):
        task = plan.tasks.get(task_id)
        if task is None:
            continue
        
        # 检查依赖
        deps = plan.dependencies.get(task_id, [])
        if not self._dependencies_satisfied(deps, results):
            continue
        
        # 检查条件分支 ← 增强点
        if not self._branch_allows(task_id, plan, results, context):
            continue
        
        ready.append(task)
    return ready
```

### Step 5: 集成测试

**文件**: `test/test_agent/test_integration.py`

```python
class TestSupervisorEmbedWorkflow:
    """Supervisor 内嵌 Workflow — 核心嵌套场景。"""
    
    async def test_supervisor_embeds_workflow(self):
        # 1. 创建 WorkflowOrchestrator (3-task DAG)
        # 2. 包装为 OrchestratorAsAgent
        # 3. 注册到 Supervisor 的 AgentRegistry
        # 4. Supervisor LLM 规划时分配任务给 WorkflowAgent
        # 5. 验证 Workflow 子结果被聚合后交给 Supervisor
        ...

class TestCouncilEmbedWorkflow:
    """Council 内嵌 Workflow — 每个投票 Agent 本身是 Workflow。"""
    ...
```

---

## 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `src/agent/composite.py` | OrchestratorAsAgent |
| 修改 | `src/agent/strategies.py` | +SupervisorStrategy +CouncilStrategy +LLMPlanner +LLMReviewer +LLMJudge |
| 修改 | `src/agent/__init__.py` | 导出新增类 |
| 新建 | `test/test_agent/test_composite.py` | OrchestratorAsAgent 测试 |
| 新建 | `test/test_agent/test_supervisor.py` | Supervisor 测试 |
| 新建 | `test/test_agent/test_council.py` | Council 测试 |
| 新建 | `test/test_agent/test_integration.py` | 嵌套场景测试 |
| 修改 | `test/test_agent/test_strategies.py` | WorkflowStrategy 增强后补充测试 |

---

## 架构全景 (Phase 2 完成后)

```
┌──────────────────────────────────────────────────────────────────┐
│                    Orchestrator (纯机械调度引擎)                  │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
 ControlStrategy (抽象)
    ├── SupervisorStrategy ✅ (Phase 2)     ← LLM 动态规划
    ├── WorkflowStrategy ✅ (Phase 1+2增强)  ← 静态 DAG + 条件分支
    └── CouncilStrategy ✅ (Phase 2)        ← 并行投票 + 裁决
    (Handoff 是 Agent 行为，由任意 Strategy 下 Agent 触发)
       │
       ▼
 BaseAgent (抽象)
    ├── 普通 Agent (ReActAgent) ✅
    └── OrchestratorAsAgent ✅ (Phase 2)    ← 递归组合钥匙
       │                                        内含 ResultAggregator ✅
       ▼
基础设施 (全部 ✅):
   AgentRegistry │ MessageBus │ ExecutionContext │ CompensationRegistry
   ErrorStrategy (4种) │ ConditionEvaluator ✅ │ LLMPlanner ✅ │ LLMJudge ✅
```

---

## 验证标准

```bash
# Phase 2 全部测试通过
python3 -m pytest test/test_agent/ -v --asyncio-mode=auto

# 预计新增 ~20 个测试用例，总测试数 ~110+
# 现有 90 个测试不能有任何退化
```

---

## 风险评估

| 风险 | 缓解 |
|------|------|
| SupervisorStrategy 需要 LLM 调用 | Phase 2 使用 StubLLMPlanner，不接入真实 ResourcePool |
| Council 裁决逻辑复杂 | Phase 2 使用 SimpleJudge (多数投票)，Phase 3 再接入 LLM |
| 嵌套组合的 context 传递 | ExecutionContext 已是引用传递，递归时自然共享 |
| CompensationRegistry 递归隔离 | 每个 Orchestrator 已有独立实例 ✅ |
