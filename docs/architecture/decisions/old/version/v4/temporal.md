# HpAgent 引入 Temporal Workflow 重构技术决策报告

---

## 文档说明

本报告基于对 [HpAgent](https://github.com/k1po9/HpAgent) 项目架构的深入分析，结合 Temporal 工作流引擎的技术特性，给出引入 Temporal 进行重构的**可行性评估、实施方案、风险分析及最终决策**。本版本已整合所有反馈建议（依赖注入示例、Continue-As-New 实现、测试策略细化、可观测性规划），形成最终决策文本。

---

## 一、HpAgent 现状架构分析

### 1.1 项目定位

HpAgent 是一个基于 Python 3.11+ 的智能对话代理框架，采用分层架构设计，支持多渠道消息接入、灵活的工具调用机制、完善的会话管理和模型降级策略。项目代码量适中，架构清晰，是重构的良好基础。

### 1.2 核心分层结构

| 层级 | 职责 | 核心组件 |
|------|------|----------|
| **应用入口层** | 系统初始化与启动 | `AgentApplication` (main.py) |
| **渠道层** | 对接外部消息源，屏蔽协议差异 | `IChannel` 接口，`ConsoleChannel`、`NapCatChannel` |
| **编排层** | 协调各组件工作，维护任务状态机 | `Orchestrator` (orchestrator.py) |
| **执行层** | 驱动推理循环，调用模型 API | `Harness` (harness.py) |
| **会话层** | 管理会话生命周期与事件持久化 | `SessionManager`、`FileSessionRepository` |
| **资源层/沙箱层** | 管理模型客户端、工具执行环境 | `ResourcePool`、`SandboxManager` |

### 1.3 核心推理流程（Agentic Loop）

```
用户消息 → Orchestrator.receive_request()
  → 创建/获取 Session → 记录 USER_MESSAGE 事件
  → Orchestrator.process_session()
    → Harness.wake() 进入推理循环：
      ① 从 Session 获取历史事件
      ② HarnessContextBuilder 构建上下文（渠道感知）
      ③ 获取可用工具列表（跨沙箱聚合）
      ④ 调用模型 API（ResourcePool 含降级策略）
      ⑤ 保存 MODEL_MESSAGE 事件
      ⑥ 若有 tool_calls → 路由到对应沙箱执行 → 保存 TOOL_RESULT 事件
      ⑦ 循环直到 stop_reason=END_TURN 或达到最大轮次
```

### 1.4 关键特性

- **依赖注入与可测试性**：所有外部依赖通过构造函数传入，便于 Mock。
- **多渠道支持**：通过 `IChannel` 抽象，支持 Console、Web、NapCat。
- **灵活工具体系**：Native、MCP 协议、Skill 三种类型，注册表模式。
- **模型降级策略**：`ResourcePool` 按降级链自动切换备用模型。
- **错误处理与重试**：层次化错误类型 + `RetryPolicy`（指数退避、线性退避、固定延迟）。
- **会话状态管理**：会话创建、事件记录、回滚、归档，基于文件系统 JSON 持久化。
- **并发控制**：`RLock` 保护共享状态。

### 1.5 当前架构的局限性

1. **单进程内存模型**：所有状态存于内存，进程重启后丢失。
2. **手动状态持久化**：文件系统 JSON 存储缺乏 ACID 事务和分布式一致性。
3. **自建重试机制**：无法处理进程崩溃后的恢复。
4. **无分布式支持**：Orchestrator、Harness、Sandbox 耦合在同一进程，无法横向扩展。
5. **缺乏可观测性**：仅标准 logging 输出，无执行历史可视化或工作流追踪。

---

## 二、Temporal 技术评估

### 2.1 核心概念

| 概念 | 说明 |
|------|------|
| **Workflow** | 确定性编排逻辑，定义业务流程。 |
| **Activity** | 非确定性操作（API 调用、文件 I/O 等），可重试、超时、心跳上报。 |
| **Worker** | 监听任务队列，执行 Workflow 和 Activity 的进程。 |
| **Task Queue** | 任务调度队列。 |
| **Event History** | 全量执行事件记录，支持重放恢复。 |

### 2.2 核心优势

- **持久化执行**：基于事件溯源，Worker 崩溃后可被其他 Worker 接管恢复。
- **内置重试机制**：Activity 原生支持灵活的重试策略。
- **工作流可见性**：Temporal Web UI 提供执行历史、状态监控、搜索和调试。
- **分布式架构**：Workflow 和 Activity 可分布在不同 Worker，支持横向扩展。
- **与 AI Agent 天然契合**：官方提供多个 Agentic Loop 示例（包括 Durable Agent with Tools 等）。

### 2.3 与 HpAgent 映射关系

| HpAgent 组件 | 映射到 Temporal | 理由 |
|-------------|----------------|------|
| **Orchestrator** | ✅ Workflow | 编排流程的核心逻辑 |
| **Harness.wake()** | ✅ Workflow Loop | 推理循环在本 Workflow 内执行 |
| **模型 API 调用** | ✅ Activity | 外部网络请求 |
| **工具执行** | ✅ Activity | 沙箱执行，可能涉及 I/O |
| **会话管理** | ⚠️ 可简化 | Event History 替代事件持久化 |
| **资源池** | ✅ 保持独立 | 作为 Activity 依赖的外部服务 |
| **渠道适配器** | ✅ 保持独立 | 消息入口/出口，通过 Activity 或直接调用 |
| **重试策略** | ✅ 替换为 Temporal Retry | 使用 Temporal 原生重试 |

---

## 三、重构方案设计

### 3.1 重构目标

- 引入 Temporal 作为编排引擎，提供持久化执行和自动故障恢复。
- 保留项目的分层架构、依赖注入、渠道抽象和工具注册表。
- 最小化改动范围：重点重构编排层和执行层，会话层由 Temporal 事件历史替代。
- 保持 Python 3.11+ 技术栈。

### 3.2 整体架构变化

```
┌──────────────────────────────────────────────────┐
│              应用入口层 (main.py)                  │
├──────────────────────────────────────────────────┤
│              渠道层 (Channels)                    │
│  ConsoleChannel │ NapCatChannel │ WebChannel     │
├──────────────────────────────────────────────────┤
│         编排层 (Temporal Workflows)               │
│  ┌────────────────────────────────────────────┐  │
│  │ AgentWorkflow (替代 Orchestrator)          │  │
│  └────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────┤
│         执行层 (Temporal Activities)              │
│  call_model_activity  │ execute_tool_activity     │
│  build_context_activity│ send_response_activity   │
├──────────────────────────────────────────────────┤
│         会话层 (简化)                             │
│  SessionState (Pydantic Model) + Temporal Query   │
├──────────────────────────────────────────────────┤
│    资源层 / 沙箱层 (保持不变)                     │
│  ResourcePool │ SandboxManager │ ToolRegistry     │
└──────────────────────────────────────────────────┘
```

**关键变化：**

- 编排层：`Orchestrator` 被 `AgentWorkflow` 替代。
- 执行层：`Harness` 关键方法拆为独立 Activities。
- 会话层：`FileSessionRepository` 的持久化功能由 Temporal 事件历史取代，保留 `Session` 数据模型用于 Workflow 内部状态。
- 渠道层/资源层/沙箱层：保持原有实现。

### 3.3 核心 Workflow 设计

#### AgentWorkflow 实现

```python
# workflows/agent_workflow.py
from temporalio import workflow
from datetime import timedelta
from typing import List, Dict, Any
from temporalio.common import RetryPolicy

@workflow.defn
class AgentWorkflow:
    def __init__(self):
        self._events: List[Dict[str, Any]] = []
        self._max_turns = 20
        self._turn_count = 0
        self._completed = False

    @workflow.run
    async def run(self, user_message: Dict[str, Any]) -> Dict[str, Any]:
        self._events.append({
            "type": "USER_MESSAGE",
            "content": user_message["content"],
            "sender_id": user_message.get("sender_id"),
        })

        while self._turn_count < self._max_turns:
            self._turn_count += 1

            # 上下文构建
            context = await workflow.execute_activity(
                "build_context_activity",
                args=[self._events, user_message.get("channel_type")],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # 获取工具
            tools = await workflow.execute_activity(
                "get_available_tools_activity",
                args=[],
                start_to_close_timeout=timedelta(seconds=10),
            )

            # 调用模型 (含重试策略)
            model_response = await workflow.execute_activity(
                "call_model_activity",
                args=[context, tools],
                start_to_close_timeout=timedelta(seconds=60),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_interval=timedelta(seconds=60),
                    maximum_attempts=3,
                ),
            )

            self._events.append({
                "type": "MODEL_MESSAGE",
                "content": model_response["content"],
                "tool_calls": model_response.get("tool_calls", []),
            })

            # 处理工具调用
            if model_response.get("tool_calls"):
                for tool_call in model_response["tool_calls"]:
                    result = await workflow.execute_activity(
                        "execute_tool_activity",
                        args=[tool_call["name"], tool_call["arguments"]],
                        start_to_close_timeout=timedelta(seconds=30),
                        retry_policy=RetryPolicy(
                            initial_interval=timedelta(seconds=1),
                            maximum_attempts=2,
                        ),
                    )
                    self._events.append({
                        "type": "TOOL_RESULT",
                        "tool_call_id": tool_call["id"],
                        "result": result,
                    })
            else:
                break

        # 发送响应
        await workflow.execute_activity(
            "send_response_activity",
            args=[model_response["content"], user_message],
            start_to_close_timeout=timedelta(seconds=10),
        )

        self._completed = True
        return {"status": "completed", "content": model_response["content"]}

    @workflow.query
    def get_events(self) -> List[Dict[str, Any]]:
        return self._events

    @workflow.query
    def get_status(self) -> Dict[str, Any]:
        return {
            "turns": self._turn_count,
            "completed": self._completed,
            "event_count": len(self._events),
        }

    @workflow.signal
    async def cancel_session(self):
        self._completed = True
```

### 3.4 会话层简化与长会话处理

#### 方案 A：Workflow 内部状态（推荐）

会话事件直接存储在 `_events` 中，借助 Temporal 的自动持久化实现故障恢复。外部通过 Query 查询。

**缺点**：单个 Workflow 事件历史过大可能影响性能。Temporal 建议事件总大小不超过 50KB。为此需引入 **Continue-As-New** 机制。

#### Continue-As-New 实现

当轮次过多或事件体积接近上限时，启动新的 Workflow 继续执行。

```python
# 在 AgentWorkflow.run() 循环内添加检测
if self._turn_count >= 50 or len(str(self._events)) > 45 * 1024:
    return await workflow.continue_as_new(
        args=[{
            "continue_from_events": self._events,
            "continue_from_turn": self._turn_count,
            "user_message": user_message
        }]
    )
```

**注意**：新 Workflow 的 `run()` 方法需能识别 `continue_from_events` 参数，恢复之前的事件历史。

### 3.5 Activities 设计

```python
# activities/agent_activities.py
from temporalio import activity
from typing import List, Dict, Any

# 这些依赖在 Worker 启动时通过 set_dependencies 注入
_context_builder = None
_resource_pool = None
_sandbox_manager = None
_channel = None

def set_dependencies(ctx_builder, res_pool, sandbox_mgr, channel=None):
    global _context_builder, _resource_pool, _sandbox_manager, _channel
    _context_builder = ctx_builder
    _resource_pool = res_pool
    _sandbox_manager = sandbox_mgr
    _channel = channel

@activity.defn
async def call_model_activity(context: List[Dict], tools: List[Dict]) -> Dict:
    response = await _resource_pool.generate(
        messages=context,
        tools=tools if tools else None,
        stream=False,
    )
    return {
        "content": response.content,
        "tool_calls": [tc.to_dict() for tc in (response.tool_calls or [])],
        "stop_reason": response.stop_reason.value,
    }

@activity.defn
async def execute_tool_activity(tool_name: str, arguments: Dict) -> Dict:
    for sandbox_info in _sandbox_manager.list_sandboxes():
        if sandbox_info["status"] != "active":
            continue
        sandbox = _sandbox_manager.get_sandbox(sandbox_info["sandbox_id"])
        if sandbox.has_tool(tool_name):
            result = await sandbox.execute(tool_name, arguments)
            return result.to_dict() if hasattr(result, 'to_dict') else {"output": str(result), "error": None}
    return {"output": None, "error": f"Tool '{tool_name}' not found"}
```

### 3.6 Worker 与依赖注入实现

Worker 启动时需完成所有依赖的初始化并注册 Activities。

```python
# worker.py
import asyncio
from temporalio.client import Client
from temporalio.worker import Worker
from resources.resource_pool import ResourcePool
from sandbox.sandbox_manager import SandboxManager
from harness.context_builder import HarnessContextBuilder
from activities.agent_activities import set_dependencies

async def init_dependencies(config: dict):
    # 1. 资源池与模型降级链
    credential_manager = CredentialManager()
    credential_manager.register_model_chain(config["models"])
    resource_pool = ResourcePool(credential_manager)
    await resource_pool.initialize_models()

    # 2. 沙箱管理器与默认工具
    sandbox_manager = SandboxManager()
    default_tools = ToolFactory.create_default_tools()
    await sandbox_manager.create_sandbox(default_tools)

    # 3. 上下文构建器
    context_builder = HarnessContextBuilder()

    return resource_pool, sandbox_manager, context_builder

async def main():
    config = load_config()
    pool, sandbox_mgr, ctx_builder = await init_dependencies(config)
    set_dependencies(ctx_builder, pool, sandbox_mgr)

    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue="hpagent-task-queue",
        workflows=[AgentWorkflow],
        activities=[...],  # 注册所有 Activity
    )

    # 同时启动渠道监听
    napcat = NapCatChannel()
    async with worker:
        await napcat.start_monitor(lambda msg: handle_message(client, msg))
        await asyncio.Future()
```

### 3.7 测试策略细化

#### Workflow 单元测试

使用 `temporalio.testing.WorkflowEnvironment` 模拟 Activity 返回，验证分支逻辑。

```python
import pytest
from temporalio.testing import WorkflowEnvironment
from workflows.agent_workflow import AgentWorkflow

@pytest.mark.asyncio
async def test_agent_workflow_with_tool_call():
    async with WorkflowEnvironment.local() as env:
        # 模拟模型调用返回工具调用
        def mock_call_model(ctx, context, tools):
            return {
                "content": "",
                "tool_calls": [{"id": "1", "name": "calculator", "arguments": {"a": 1, "b": 2}}]
            }
        # 模拟工具执行
        def mock_execute_tool(ctx, tool_name, arguments):
            return {"output": 3, "error": None}

        env.register_activity("call_model_activity", mock_call_model)
        env.register_activity("execute_tool_activity", mock_execute_tool)

        result = await env.client.execute_workflow(
            AgentWorkflow.run,
            args=[{"content": "1+2=?", "sender_id": "test"}],
            id="test-wf-1",
            task_queue="test-queue",
        )
        assert result["status"] == "completed"
        # 可通过 Query 验证事件数量
```

#### 集成测试

通过 Docker Compose 启动 Temporal Server，运行完整 Worker，发送真实渠道消息验证端到端流程。

---

## 四、风险评估与缓解措施

### 4.1 技术风险

| 风险 | 缓解措施 |
|------|----------|
| 学习成本 | 团队完成 Temporal 101/102 培训（各2-4h） |
| 确定性限制 | 所有 I/O 操作放入 Activity；Workflow 中仅使用确定性 API |
| 事件历史膨胀 | 实施 Continue-As-New，设置合理保留策略 |
| 运维复杂度 | 使用 Docker Compose 自托管 Temporal |

### 4.2 性能风险

| 风险 | 缓解措施 |
|------|----------|
| Activity 调度延迟 | Temporal Server 与 Worker 同机房部署；合并可批量工具调用 |
| Workflow 重放延迟 | 优化 Workflow 内逻辑，避免重型计算 |

### 4.3 兼容性与测试风险

| 风险 | 缓解措施 |
|------|----------|
| 现有接口破坏 | 暂保留原有 Session 接口，后续逐步弃用 |
| 测试覆盖缺失 | 使用 Temporal 测试框架编写 Workflow 单元测试，并建立集成测试套件 |
| 历史数据兼容 | 若需迁移，编写一次性脚本将 JSON 事件重放为 Workflow 启动参数 |

---

## 五、决策建议

### 5.1 推荐方案：分两阶段引入 Temporal

**阶段一（核心流程迁移，2-4 周）**

- 实现 `AgentWorkflow`，将 Agentic Loop 迁移至 Temporal。
- 将模型调用和工具执行包裹为 Activities。
- 保留现有 `SessionManager`、`ResourcePool` 等，作为依赖注入。
- 在单 Worker 环境验证功能，提供完整单元测试。

**预期收益**：
- 自动重试故障恢复
- 执行历史可视化
- 代码改动约 300 行

**阶段二（全量优化，4-6 周）**

- 用 Temporal Event History 全面替代文件事件持久化。
- 完善 Continue-As-New 及长会话管理。
- 部署多 Worker 水平扩展。
- 接入 Prometheus + Grafana 监控，关联 Workflow ID 日志。

**预期收益**：
- 完整持久化执行
- 进程故障后 Automatic Recovery
- 水平扩展能力

### 5.2 不建议事项

- **不全量重写**：保留渠道层、资源层、沙箱层，避免高风险重写。
- **不引入过度设计**：阶段一保持简单，避免为未验证的需求提前实现复杂功能。

---

## 附录：关键文件索引

| 文件 | 行数 | 职责 |
|------|------|------|
| `src/main.py` | 61 | 应用入口 |
| `src/orchestration/orchestrator.py` | 68 | 编排核心（待重构） |
| `src/harness/harness.py` | 64 | 推理循环 |
| `src/harness/context_builder.py` | 221 | 上下文构建 |
| `src/session/session_manager.py` | 111 | 会话管理 |
| `src/session/repositories.py` | 51 | 文件持久化 |
| `src/sandbox/sandbox.py` | 31 | 沙箱 |
| `src/sandbox/sandbox_manager.py` | 27 | 沙箱管理 |
| `src/sandbox/tools/base.py` | 23 | 工具基类 |
| `src/sandbox/tools/factory.py` | 37 | 工具工厂 |
| `src/sandbox/tools/registry.py` | 21 | 工具注册 |
| `src/sandbox/channels/base.py` | 24 | 渠道接口 |
| `src/resources/resource_pool.py` | 57 | 模型资源池 |
| `src/common/types.py` | 67 | 类型定义 |
| `src/common/errors.py` | 37 | 错误体系 |
| `src/common/interfaces.py` | 50 | 接口定义 |
| `src/orchestration/retry_policy.py` | 32 | 重试策略（将被替代） |

---

**最终结论**：本报告建议坚定地分阶段采用 Temporal 重构 HpAgent 的编排与执行层，在保留现有成熟组件的基础上获得持久化执行、自动故障恢复和可观测性等关键能力。方案具备技术可行性和明确的风险控制措施，可立即启动阶段一实施。