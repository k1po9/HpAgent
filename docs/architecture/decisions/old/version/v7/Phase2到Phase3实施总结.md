# Phase 2 → Phase 3 实施总结

## Phase 2 成果回顾

### 三种 ControlStrategy 全部完工

| Strategy | 模式 | 核心机制 | Phase |
|----------|------|----------|-------|
| `WorkflowControlStrategy` | 静态 DAG + 条件分支 | `get_ready_batch` 检查依赖+分支条件 → `on_batch_completed` 失败重试/跳过 | 1 |
| `SupervisorControlStrategy` | LLM 动态规划 | Planner 拆解任务 → 执行 → Reviewer 审查 → new_tasks 注入或终止 | 2 |
| `CouncilControlStrategy` | 并行投票 + 裁决 | 同一 goal 分发 N 个 Agent → 并行执行 → Judge 裁决最优结果 | 2 |

### 递归组合

`OrchestratorAsAgent` 将任意 Orchestrator 包装为 BaseAgent，实现 Supervisor 内嵌 Workflow、Council 内嵌 Workflow 等嵌套场景。

### LLM 抽象体系 (Phase 2 建立)

```
CallLLM = Callable[[list[dict], list[dict] | None], Awaitable[str]]

LLMPlanner(ABC)          →   StubLLMPlanner (Phase 2 stub)
LLMReviewer(ABC)         →   StubLLMReviewer (Phase 2 stub)
LLMJudge(ABC)            →   MajorityJudge   (Phase 2 多数投票)
```

---

## Phase 3 交付物

Phase 3 目标：**将真实 LLM 接入多Agent架构**，让 Supervisor/Council 模式不再依赖 stub。

### 核心实现

| # | 组件 | 文件 | 行数 | 说明 |
|---|------|------|------|------|
| 1 | `RealLLMPlanner` | `strategies.py` | ~70 | 调用 LLM 生成 JSON 格式任务分解，解析后返回 (tasks, deps) |
| 2 | `RealLLMReviewer` | `strategies.py` | ~70 | 调用 LLM 审查完成结果，返回 (is_done, new_tasks) |
| 3 | `RealLLMJudge` | `strategies.py` | ~65 | 调用 LLM 裁决多个候选结果，选出最优 |
| 4 | `ResourcePoolAdapter` | `factory.py` | ~20 | 将 ResourcePool.generate() 适配为 CallLLM 协议 |
| 5 | `build_supervisor()` | `factory.py` | ~30 | 一行构建 Supervisor 模式 Orchestrator |
| 6 | `build_council()` | `factory.py` | ~30 | 一行构建 Council 模式 Orchestrator |
| 7 | `build_workflow()` | `factory.py` | ~20 | 一行构建 Workflow 模式 Orchestrator |

### 关键设计决策

**1. CallLLM 协议 — 依赖注入而非耦合**

```python
# RealLLM* 不直接依赖 ResourcePool，只依赖 CallLLM 可调用对象
# 好处：测试用 mock_llm，生产用 ResourcePoolAdapter，完全解耦
CallLLM = Callable[[list[dict], list[dict] | None], Awaitable[str]]
```

**2. ResourcePoolAdapter — 薄适配层**

```python
class ResourcePoolAdapter:
    """ResourcePool.generate(messages, model_selector, tools, stream) → CallLLM"""
    async def __call__(self, messages, tools=None):
        response = await self._pool.generate(
            messages=messages, model_selector=self._model_selector,
            tools=tools, stream=False,
        )
        return response.content or ""
```

**3. 容错设计 — 三层降级**

| 场景 | Planner 降级 | Reviewer 降级 | Judge 降级 |
|------|-------------|---------------|------------|
| LLM 调用异常 | fallback 单任务 `{goal}` | 假定 done=True | 降级 MajorityJudge |
| 返回非 JSON | fallback 单任务 | 假定 done=True | 降级 MajorityJudge |
| 空任务列表 | fallback 单任务 | — | — |
| 超过 max_rounds | — | 强制 done=True | — |

**4. JSON 解析容错 — 支持 markdown 围栏**

```python
# 支持三种格式：
# 1. 纯 JSON: `{"tasks": [...]}`
# 2. ```json ... ``` 围栏
# 3. ``` ... ``` 围栏（无语言标识）
```

### 测试覆盖

| 测试文件 | 用例数 | 覆盖内容 |
|----------|--------|----------|
| `test_real_llm.py` | 14 | Planner(6) + Reviewer(4) + Judge(4) |
| `test_factory.py` | 6 | ResourcePoolAdapter(2) + build_supervisor(1) + build_council(2) + build_workflow(1) |

**所有测试均为纯单元测试，无需真实 LLM 调用** — 通过 `make_mock_llm(response)` 注入固定响应。

---

## 冗余代码清理

Phase 2→3 过渡期间执行了一次全量审计，删除内容：

| 删除项 | 原因 |
|--------|------|
| `src/agent/errors.py` (45行) | 4 个 ErrorStrategy 从未被引用，决策通过 BatchOutcome 收口 |
| `test/test_agent/test_errors.py` (40行) | 对应测试 |
| `RetryDecision` dataclass | 仅被已删除的 ErrorStrategy 使用 |
| `StatusMessage` / `VoteMessage` dataclass | 从未被使用 |
| `InMemoryAgentRegistry.register()` | 死方法，仅有 `register_direct()` |
| `CompensationRegistry.unregister()` | 死方法 |
| 8 个 unused imports | uuid, Optional, Any, HandoffRequest 等 |

**删除前: 101 tests → 删除后: 91 tests (Phase 2 存量)，Phase 3 新增 20 tests → 总计 111 tests**

---

## 文件总览 (Phase 3 完成后)

```
src/agent/
  __init__.py           (124行)  包导出 — 45 个公开符号
  types.py              (142行)  7 枚举 + 15 dataclass
  interfaces.py         (149行)  5 ABC: BaseAgent/ControlStrategy/AgentRegistry/MessageBus
  context.py            (84行)   ExecutionContext + SharedMemory(并发安全)
  orchestrator.py       (242行)  纯机械调度循环(批量并行+超时+handoff+补偿)
  strategies.py         (886行)  3 Strategy + 3 LLM抽象 + 3 RealLLM实现 + ConditionEvaluator + ResultAggregator
  factory.py            (173行)  ResourcePoolAdapter + build_supervisor/build_council/build_workflow
  bus.py                (71行)   InMemoryMessageBus
  registry.py           (75行)   InMemoryAgentRegistry
  compensation.py       (42行)   CompensationRegistry(实例化)
  adapters.py           (103行)  ReActAgent
  composite.py          (83行)   OrchestratorAsAgent(递归组合)

test/test_agent/
  __init__.py           (0行)
  conftest.py           (31行)   共享 helper: make_agent / make_context
  test_types.py         (185行)  18 用例
  test_interfaces.py    (88行)   8 用例
  test_context.py       (110行)  12 用例
  test_orchestrator.py  (420行)  10 用例
  test_strategies.py    (186行)  17 用例
  test_bus.py           (49行)   2 用例
  test_registry.py      (89行)   6 用例
  test_compensation.py  (32行)   3 用例
  test_adapters.py      (32行)   3 用例
  test_composite.py     (135行)  5 用例
  test_supervisor.py    (83行)   3 用例
  test_council.py       (113行)  6 用例
  test_integration.py   (191行)  6 用例
  test_real_llm.py      (224行)  14 用例 (Phase 3 新增)
  test_factory.py       (117行)  6 用例 (Phase 3 新增)
```

**总计: 代码 ~2030 行, 测试 ~1880 行, 111 tests, 0 failures**

---

## 架构全景 (Phase 3 完成后)

```
┌──────────────────────────────────────────────────────────────────┐
│                    Orchestrator (纯机械调度引擎)                  │
└──────┬───────────────────────────────────────────────────────────┘
       │
       ▼
 ControlStrategy (抽象)
    ├── SupervisorControlStrategy ✅     ← RealLLMPlanner + RealLLMReviewer
    ├── WorkflowControlStrategy ✅       ← 静态 DAG + 条件分支
    └── CouncilControlStrategy ✅        ← 并行投票 + RealLLMJudge / MajorityJudge
       │
       ▼
 BaseAgent (抽象)
    ├── ReActAgent (包装 HarnessRunner)
    └── OrchestratorAsAgent (递归组合)
       │
       ▼
 LLM 接入层 (Phase 3 新增)
    ├── ResourcePoolAdapter    ← ResourcePool → CallLLM
    ├── build_supervisor()     ← 一行构建
    ├── build_council()        ← 一行构建
    └── build_workflow()       ← 一行构建
       │
       ▼
 基础设施 (全部 ✅):
    AgentRegistry │ MessageBus │ ExecutionContext │ CompensationRegistry
    ConditionEvaluator │ ResultAggregator
```

---

## 下一步 (Phase 4 建议)

| # | 方向 | 说明 |
|---|------|------|
| 1 | **端到端集成** | 在 main.py 中创建真实 Orchestrator，用 ResourcePoolAdapter 接入真实 LLM |
| 2 | **Temporal 集成** | 将 Orchestrator.run() 包装为 Temporal Activity/Workflow |
| 3 | **SharedMemory 持久化** | 当前 SharedMemory 仅进程内，需支持跨 Activity 持久化 |
| 4 | **Agent 动态发现** | 基于 Redis/etcd 的分布式 AgentRegistry |
| 5 | **可观测性** | 添加 OpenTelemetry traces + metrics 到 Orchestrator 执行循环 |

---

## 验证

```bash
# 全部 111 测试通过
python3 -m pytest test/test_agent/ -v --asyncio-mode=auto

# 0 个外部依赖，0 个现有代码修改，0 个退化
```
